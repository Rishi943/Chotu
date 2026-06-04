"""QwenOmniBackend — async Backend over Alibaba DashScope's Qwen-Omni
Realtime WebSocket API. Sibling of GeminiLiveBackend.

The DashScope SDK (`dashscope.audio.qwen_omni.OmniRealtimeConversation`) is
synchronous and callback-driven on its own WS thread. This module owns a
small bridge that re-routes those callbacks into an asyncio.Queue on the
brain's loop so the rest of the codebase can treat it like any other
async Backend.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import AsyncIterator, Optional

from core.backend import (
    AssistantText,
    BackendError,
    Event,
    SessionEnded,
    ToolCall,
)

log = logging.getLogger(__name__)


class _QwenEventBridge:
    """Decodes raw DashScope realtime events (dicts) emitted on the SDK's WS
    thread and forwards them as typed Events onto an asyncio.Queue owned by
    the brain's event loop.

    DashScope callbacks run on a non-asyncio thread, so every queue insert
    goes through loop.call_soon_threadsafe.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self._partial_text: str = ""
        self._partial_calls: dict[str, dict] = {}
        # Tool calls finalized in the current response — deferred until
        # response.done so the brain doesn't trigger create_response while
        # the model's response is still active.
        self._pending_tool_calls: list[ToolCall] = []

    def _emit(self, event: Event) -> None:
        self.loop.call_soon_threadsafe(self.queue.put_nowait, event)

    def on_event(self, msg: dict) -> None:
        t = msg.get("type", "")
        try:
            if t == "response.text.delta" or t == "response.audio_transcript.delta":
                self._partial_text += msg.get("delta", "")
            elif t == "response.text.done" or t == "response.audio_transcript.done":
                text = msg.get("text") or self._partial_text
                self._partial_text = ""
                if text:
                    self._emit(AssistantText(text=text))
            elif t == "response.function_call_arguments.delta":
                call_id = msg.get("call_id", "")
                if not call_id:
                    return
                entry = self._partial_calls.setdefault(
                    call_id, {"name": msg.get("name", ""), "arg_buf": ""}
                )
                if msg.get("name"):
                    entry["name"] = msg["name"]
                entry["arg_buf"] += msg.get("delta", "")
            elif t == "response.function_call_arguments.done":
                call_id = msg.get("call_id", "")
                partial = self._partial_calls.pop(call_id, {})
                name = msg.get("name") or partial.get("name", "")
                raw = msg.get("arguments")
                if raw is None:
                    raw = partial.get("arg_buf", "")
                try:
                    args = json.loads(raw) if raw else {}
                except json.JSONDecodeError as e:
                    log.warning("tool call %s: bad JSON args %r (%s)", name, raw, e)
                    args = {}
                self._pending_tool_calls.append(ToolCall(id=call_id, name=name, args=args))
            elif t == "response.done":
                # Flush any tool calls accumulated during this response.
                for call in self._pending_tool_calls:
                    self._emit(call)
                self._pending_tool_calls.clear()
            elif t == "error":
                err = msg.get("error", msg)
                err_msg = str(err.get("message", err) if isinstance(err, dict) else err)
                # Benign races we recover from rather than tearing down:
                #   - "Conversation already has an active response": fires when
                #     the brain dispatches multiple tool calls from a single
                #     response and calls create_response per result. The model
                #     already has each tool output via create_item and will
                #     continue the active response.
                if "active response" in err_msg.lower():
                    log.info("qwen recoverable error (ignored): %s", err_msg)
                    return
                self._emit(BackendError(message=err_msg, recoverable=False))
            else:
                log.debug("qwen unknown event type %r", t)
        except Exception as e:
            log.exception("qwen bridge on_event failed")
            self._emit(BackendError(message=f"bridge decode error: {e}", recoverable=False))

    def on_close(self, code: int, reason: str) -> None:
        self._emit(SessionEnded(reason=f"ws closed (code={code}): {reason}"))

    def on_error(self, err: object) -> None:
        self._emit(BackendError(message=str(err), recoverable=False))


class QwenOmniBackend:
    def __init__(
        self,
        *,
        system_prompt: str,
        tool_schemas: list[dict],
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        ws_url: Optional[str] = None,
    ) -> None:
        self._system = system_prompt
        self._tools = tool_schemas or []
        self._model = model or os.getenv("PALIV_QWEN_OMNI_MODEL", "qwen3.5-omni-flash-realtime")
        self._api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self._ws_url = ws_url or os.getenv("PALIV_QWEN_OMNI_WS_URL", "")
        if not self._api_key:
            raise ValueError("DASHSCOPE_API_KEY not set")

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._bridge: Optional[_QwenEventBridge] = None
        self._conv = None
        self._closed = asyncio.Event()
        # 100 ms of silence at 16 kHz mono PCM16, pre-encoded to base64.
        # Streamed continuously by a background task so the audio-input
        # timeline (which Qwen-Omni Realtime treats as the master clock)
        # always stays ahead of image inserts. Without this the server
        # rejects images with "Error append image before append audio".
        self._silence_b64 = base64.b64encode(b"\x00" * (16000 // 10 * 2)).decode("ascii")
        self._silence_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        import dashscope
        from dashscope.audio.qwen_omni import (
            OmniRealtimeConversation,
            OmniRealtimeCallback,
            MultiModality,
        )

        self._loop = asyncio.get_running_loop()
        self._bridge = _QwenEventBridge(self._loop)

        bridge = self._bridge

        class _Callback(OmniRealtimeCallback):  # type: ignore[misc, valid-type]
            def on_open(self) -> None:
                log.info("Qwen-Omni WS opened")

            def on_event(self, msg) -> None:  # type: ignore[override]
                if isinstance(msg, dict):
                    bridge.on_event(msg)
                else:
                    try:
                        bridge.on_event(dict(msg))
                    except Exception:
                        log.debug("qwen non-dict event ignored: %r", msg)

            def on_close(self, close_status_code, close_msg) -> None:  # type: ignore[override]
                bridge.on_close(int(close_status_code or 0), str(close_msg or ""))

            def on_error(self, err) -> None:  # type: ignore[override]
                bridge.on_error(err)

        dashscope.api_key = self._api_key

        kwargs = {"model": self._model, "callback": _Callback()}
        if self._ws_url:
            kwargs["url"] = self._ws_url
        conv = OmniRealtimeConversation(**kwargs)

        await self._loop.run_in_executor(None, conv.connect)
        # Server rejects voice=null even in text-only mode, so pass a real
        # voice name. Audio output is requested by the server even when we
        # only consume the audio_transcript stream — the audio bytes are
        # ignored by our bridge.
        voice = os.getenv("PALIV_QWEN_OMNI_VOICE", "Tina")
        await self._loop.run_in_executor(
            None,
            lambda: conv.update_session(
                output_modalities=[MultiModality.TEXT],
                voice=voice,
                enable_turn_detection=False,
                instructions=self._system,
                tools=self._tools,
            ),
        )
        self._conv = conv
        self._silence_task = asyncio.create_task(self._stream_silence(), name="QwenSilencePump")
        log.info("QwenOmniBackend connected to %s", self._model)

    async def _stream_silence(self) -> None:
        """Continuously push 100 ms silence chunks every 100 ms so the
        server's audio input timeline always stays ahead of video frames."""
        assert self._conv is not None and self._loop is not None
        try:
            while not self._closed.is_set():
                try:
                    await self._loop.run_in_executor(
                        None, self._conv.append_audio, self._silence_b64
                    )
                except Exception as e:
                    log.debug("silence pump send failed: %s", e)
                    return
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise

    async def send_user_text(self, text: str) -> None:
        if not self._conv or not self._loop:
            return
        item = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }
        await self._loop.run_in_executor(None, self._conv.create_item, item)
        await self._loop.run_in_executor(None, self._conv.create_response)

    async def send_frame(self, jpeg_bytes: bytes, ts: float) -> None:
        if not self._conv or not self._loop:
            return
        b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        await self._loop.run_in_executor(None, self._conv.append_video, b64)

    async def send_tool_result(self, tool_call_id: str, result: dict) -> None:
        if not self._conv or not self._loop:
            return
        item = {
            "type": "function_call_output",
            "call_id": tool_call_id,
            "output": json.dumps(result),
        }
        await self._loop.run_in_executor(None, self._conv.create_item, item)
        await self._loop.run_in_executor(None, self._conv.create_response)

    async def events(self) -> AsyncIterator[Event]:
        assert self._bridge is not None
        while not self._closed.is_set() or not self._bridge.queue.empty():
            try:
                ev = await asyncio.wait_for(self._bridge.queue.get(), timeout=0.5)
                yield ev
            except asyncio.TimeoutError:
                continue

    async def close(self) -> None:
        self._closed.set()
        if self._silence_task:
            self._silence_task.cancel()
            try:
                await self._silence_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._conv and self._loop:
            try:
                await self._loop.run_in_executor(None, self._conv.close)
            except Exception as e:
                log.warning("Qwen close error: %s", e)
