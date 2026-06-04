"""Bridge test: feed synthetic DashScope events from a non-loop thread,
confirm they arrive on the asyncio queue in order."""

import asyncio
import threading

from core.qwen_omni_backend import _QwenEventBridge
from core.backend import AssistantText, ToolCall, SessionEnded


async def test_text_done_emits_assistant_text():
    loop = asyncio.get_running_loop()
    bridge = _QwenEventBridge(loop)
    bridge.on_event({"type": "response.text.delta", "delta": "Hello, "})
    bridge.on_event({"type": "response.text.delta", "delta": "world."})
    bridge.on_event({"type": "response.text.done", "text": "Hello, world."})

    ev = await asyncio.wait_for(bridge.queue.get(), timeout=1.0)
    assert isinstance(ev, AssistantText)
    assert ev.text == "Hello, world."


async def test_function_call_done_emits_toolcall():
    loop = asyncio.get_running_loop()
    bridge = _QwenEventBridge(loop)
    bridge.on_event({
        "type": "response.function_call_arguments.done",
        "call_id": "call-1",
        "name": "move",
        "arguments": '{"direction": "forward", "steps": 2}',
    })

    ev = await asyncio.wait_for(bridge.queue.get(), timeout=1.0)
    assert isinstance(ev, ToolCall)
    assert ev.id == "call-1"
    assert ev.name == "move"
    assert ev.args == {"direction": "forward", "steps": 2}


async def test_threadsafe_put_from_other_thread():
    """The real SDK runs callbacks on its WS thread. Verify the bridge
    routes events from a non-loop thread without deadlock or loss."""
    loop = asyncio.get_running_loop()
    bridge = _QwenEventBridge(loop)

    def producer():
        for i in range(5):
            bridge.on_event({"type": "response.text.done", "text": f"msg{i}"})

    t = threading.Thread(target=producer)
    t.start()
    t.join()

    received = []
    for _ in range(5):
        ev = await asyncio.wait_for(bridge.queue.get(), timeout=1.0)
        received.append(ev.text)
    assert received == [f"msg{i}" for i in range(5)]


async def test_close_emits_session_ended():
    loop = asyncio.get_running_loop()
    bridge = _QwenEventBridge(loop)
    bridge.on_close(1000, "normal closure")
    ev = await asyncio.wait_for(bridge.queue.get(), timeout=1.0)
    assert isinstance(ev, SessionEnded)
    assert "1000" in ev.reason or "normal" in ev.reason
