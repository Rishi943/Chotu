"""GeminiLiveBackend — async Backend over Gemini 3.1 Flash Live Preview's
bidi WebSocket. One session per process. v1 disconnect policy is fail-loud:
on close or error, emit BackendError/SessionEnded and let the brain stop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator, Optional

from google import genai
from google.genai import types as gtypes

from core.backend import AssistantText, BackendError, Event, SessionEnded, ToolCall

log = logging.getLogger(__name__)


def _convert_tool_schemas(openai_tools: list[dict]) -> list[gtypes.Tool]:
    """OpenAI function-calling schema → Gemini function declarations."""
    decls = []
    for t in openai_tools or []:
        fn = t.get("function", {})
        decls.append(gtypes.FunctionDeclaration(
            name=fn["name"],
            description=fn.get("description", ""),
            parameters=fn.get("parameters", {"type": "object", "properties": {}}),
        ))
    return [gtypes.Tool(function_declarations=decls)] if decls else []


class GeminiLiveBackend:
    def __init__(
        self,
        *,
        system_prompt: str,
        tool_schemas: list[dict],
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        thinking_level: str = "minimal",
    ) -> None:
        self._system = system_prompt
        self._tools = _convert_tool_schemas(tool_schemas)
        self._model = model or os.getenv("PALIV_GEMINI_MODEL", "gemini-3.1-flash-live-preview")
        self._api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not self._api_key:
            raise ValueError("GEMINI_API_KEY not set")
        self._thinking_level = thinking_level

        self._client = genai.Client(api_key=self._api_key)
        self._session_cm = None  # the live.connect() async-cm
        self._session = None
        self._events: asyncio.Queue[Event] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()

    async def start(self) -> None:
        config = gtypes.LiveConnectConfig(
            response_modalities=["TEXT"],
            system_instruction=gtypes.Content(parts=[gtypes.Part(text=self._system)]),
            tools=self._tools,
            thinking_config=gtypes.ThinkingConfig(thinking_level=self._thinking_level),
            realtime_input_config=gtypes.RealtimeInputConfig(
                automatic_activity_detection=gtypes.AutomaticActivityDetection(disabled=True),
            ),
        )
        self._session_cm = self._client.aio.live.connect(model=self._model, config=config)
        self._session = await self._session_cm.__aenter__()
        self._reader_task = asyncio.create_task(self._reader(), name="GeminiLiveReader")
        log.info("GeminiLiveBackend connected to %s", self._model)

    async def send_user_text(self, text: str) -> None:
        if not self._session:
            return
        await self._session.send_client_content(
            turns=gtypes.Content(role="user", parts=[gtypes.Part(text=text)]),
            turn_complete=True,
        )

    async def send_frame(self, jpeg_bytes: bytes, ts: float) -> None:
        if not self._session:
            return
        await self._session.send_realtime_input(
            media=gtypes.Blob(data=jpeg_bytes, mime_type="image/jpeg"),
        )

    async def send_tool_result(self, tool_call_id: str, result: dict) -> None:
        if not self._session:
            return
        await self._session.send_tool_response(
            function_responses=[gtypes.FunctionResponse(
                id=tool_call_id,
                name=result.get("tool", ""),
                response=result,
            )],
        )

    async def events(self) -> AsyncIterator[Event]:
        while not self._closed.is_set() or not self._events.empty():
            try:
                ev = await asyncio.wait_for(self._events.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            yield ev

    async def close(self) -> None:
        self._closed.set()
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._session_cm:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception as e:
                log.warning("session close error: %s", e)

    async def _reader(self) -> None:
        assert self._session is not None
        try:
            async for response in self._session.receive():
                sc = getattr(response, "server_content", None)
                if sc is not None:
                    mt = getattr(sc, "model_turn", None)
                    if mt is not None:
                        for part in getattr(mt, "parts", []) or []:
                            txt = getattr(part, "text", None)
                            if txt:
                                await self._events.put(AssistantText(text=txt))

                tc = getattr(response, "tool_call", None)
                if tc is not None:
                    for fc in getattr(tc, "function_calls", []) or []:
                        await self._events.put(ToolCall(
                            id=getattr(fc, "id", "") or "",
                            name=getattr(fc, "name", "") or "",
                            args=dict(getattr(fc, "args", {}) or {}),
                        ))

                ga = getattr(response, "go_away", None)
                if ga is not None:
                    log.warning("Gemini goAway received: %s", ga)
                    await self._events.put(AssistantText(text="[system] goAway from server — session will end soon"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("Gemini Live reader crashed")
            await self._events.put(BackendError(message=str(e), recoverable=False))
        finally:
            await self._events.put(SessionEnded(reason="reader exited"))
