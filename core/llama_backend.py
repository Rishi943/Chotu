"""LlamaServerBackend — adapts the existing turn-based LLMClient into the
async Backend protocol used by brain.py in live-brain v1.

In stateless mode, each send_user_text triggers one chat_complete call.
Frames pushed via send_frame are buffered and attached as a deferred
multimodal user message on the NEXT turn (mirroring the deferred-vision
pattern brain.py already uses for capture_vision results — vision content
cannot follow tool results in the same turn under llama-server)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import AsyncIterator, Optional

from core.backend import AssistantText, BackendError, Event, SessionEnded, ToolCall

log = logging.getLogger(__name__)

_FRAME_BUFFER_CAP = 3


class LlamaServerBackend:
    def __init__(self, *, llm_client, tool_schemas: list[dict], system_prompt: str) -> None:
        self._llm = llm_client
        self._tools = tool_schemas
        self._system = system_prompt
        self._messages: list[dict] = [{"role": "system", "content": system_prompt}]
        self._pending_frames: list[bytes] = []
        self._events: asyncio.Queue[Event] = asyncio.Queue()
        self._closed = False

    async def start(self) -> None:
        return None

    async def send_user_text(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})
        if self._pending_frames:
            parts: list[dict] = [
                {"type": "text", "text": f"{len(self._pending_frames)} recent frames, ~1s apart, oldest first."}
            ]
            for jpeg in self._pending_frames:
                b64 = base64.b64encode(jpeg).decode("ascii")
                parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
            self._messages.append({"role": "user", "content": parts})
            self._pending_frames.clear()
        await self._run_turn()

    async def send_frame(self, jpeg_bytes: bytes, ts: float) -> None:
        self._pending_frames.append(jpeg_bytes)
        if len(self._pending_frames) > _FRAME_BUFFER_CAP:
            self._pending_frames.pop(0)

    async def send_tool_result(self, tool_call_id: str, result: dict) -> None:
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result),
        })
        # Tool result triggers a follow-up turn so the model can react —
        # matches the existing brain.py stateless loop.
        await self._run_turn()

    async def events(self) -> AsyncIterator[Event]:
        while not self._closed:
            try:
                ev = await asyncio.wait_for(self._events.get(), timeout=0.5)
                yield ev
            except asyncio.TimeoutError:
                continue
        yield SessionEnded(reason="closed")

    async def close(self) -> None:
        self._closed = True

    async def _run_turn(self) -> None:
        try:
            resp = await self._llm.chat_complete(self._messages, self._tools)
        except Exception as e:
            log.exception("LLM call failed")
            await self._events.put(BackendError(message=str(e), recoverable=True))
            return

        if not resp.choices:
            await self._events.put(BackendError(message="empty choices", recoverable=True))
            return

        msg = resp.choices[0].message
        if msg.content:
            await self._events.put(AssistantText(text=msg.content))

        self._messages.append(self._llm.format_assistant_message(resp))

        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            await self._events.put(ToolCall(id=tc.id, name=tc.function.name, args=args))
