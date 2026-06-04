"""LlamaServerBackend wraps LLMClient. It treats every send_user_text as one
turn: build messages from accumulated context, call chat_complete, emit
AssistantText + ToolCall events. send_frame attaches the frame as a deferred
multimodal user message on the NEXT turn (matching the existing capture_vision
deferral pattern in brain.py)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.llama_backend import LlamaServerBackend
from core.backend import ToolCall, AssistantText


def _fake_llm_response(*, text: str | None = None, tool_calls: list | None = None):
    """Build a fake LLMResponse-shaped object."""
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = tool_calls
    choice = MagicMock(message=msg)
    resp = MagicMock(choices=[choice])
    return resp


async def _drain_until(backend, predicate, *, timeout=2.0):
    collected = []
    done = asyncio.Event()

    async def consume():
        async for ev in backend.events():
            collected.append(ev)
            if predicate(ev):
                done.set()
                return

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    finally:
        consumer.cancel()
        try:
            await consumer
        except (asyncio.CancelledError, Exception):
            pass
    return collected


async def test_send_user_text_emits_assistant_text():
    llm = MagicMock()
    llm.chat_complete = AsyncMock(return_value=_fake_llm_response(text="hello"))
    llm.format_assistant_message = MagicMock(return_value={"role": "assistant", "content": "hello"})

    b = LlamaServerBackend(llm_client=llm, tool_schemas=[], system_prompt="SYS")
    await b.start()
    await b.send_user_text("hi")
    events = await _drain_until(b, lambda ev: isinstance(ev, AssistantText))
    assert any(isinstance(ev, AssistantText) and ev.text == "hello" for ev in events)
    await b.close()


async def test_send_user_text_emits_tool_calls():
    llm = MagicMock()
    tc_mock = MagicMock()
    tc_mock.id = "fc-1"
    tc_mock.function.name = "speak"
    tc_mock.function.arguments = '{"text": "hi"}'
    llm.chat_complete = AsyncMock(return_value=_fake_llm_response(tool_calls=[tc_mock]))
    llm.format_assistant_message = MagicMock(return_value={"role": "assistant", "tool_calls": []})

    b = LlamaServerBackend(llm_client=llm, tool_schemas=[], system_prompt="SYS")
    await b.start()
    await b.send_user_text("do thing")
    events = await _drain_until(b, lambda ev: isinstance(ev, ToolCall))
    tcs = [e for e in events if isinstance(e, ToolCall)]
    assert len(tcs) == 1
    assert tcs[0].id == "fc-1"
    assert tcs[0].name == "speak"
    assert tcs[0].args == {"text": "hi"}
    await b.close()


async def test_send_frame_is_buffered_for_next_turn():
    """Frames are buffered and flushed as a deferred multimodal user message
    on the next send_user_text. Verify accepted without error and not emitted
    as an event."""
    llm = MagicMock()
    llm.chat_complete = AsyncMock(return_value=_fake_llm_response(text="ok"))
    llm.format_assistant_message = MagicMock(return_value={"role": "assistant", "content": "ok"})

    b = LlamaServerBackend(llm_client=llm, tool_schemas=[], system_prompt="SYS")
    await b.start()
    await b.send_frame(b"\xff\xd8\xff\xd9", ts=1.0)
    # No events expected from send_frame alone — the buffer is silent until a turn fires.
    await b.close()


async def test_frames_attached_on_next_turn():
    """When send_user_text fires, any pending frames go into the messages
    as a multimodal user content list with image_url parts."""
    llm = MagicMock()
    llm.chat_complete = AsyncMock(return_value=_fake_llm_response(text="ok"))
    llm.format_assistant_message = MagicMock(return_value={"role": "assistant", "content": "ok"})

    b = LlamaServerBackend(llm_client=llm, tool_schemas=[], system_prompt="SYS")
    await b.start()
    await b.send_frame(b"\xff\xd8jpeg1", ts=1.0)
    await b.send_frame(b"\xff\xd8jpeg2", ts=2.0)
    await b.send_user_text("look")

    # Inspect the messages list passed into chat_complete
    sent_messages = llm.chat_complete.call_args[0][0]
    # Should be: [system, user "look", user [text + 2 image parts]]
    image_messages = [m for m in sent_messages if isinstance(m.get("content"), list)]
    assert len(image_messages) == 1
    parts = image_messages[0]["content"]
    image_parts = [p for p in parts if isinstance(p, dict) and p.get("type") == "image_url"]
    assert len(image_parts) == 2
    await b.close()


async def test_frame_buffer_cap():
    """Pending-frame buffer caps at 3 to match live-mode sampler."""
    llm = MagicMock()
    llm.chat_complete = AsyncMock(return_value=_fake_llm_response(text="ok"))
    llm.format_assistant_message = MagicMock(return_value={"role": "assistant", "content": "ok"})

    b = LlamaServerBackend(llm_client=llm, tool_schemas=[], system_prompt="SYS")
    await b.start()
    for i in range(5):
        await b.send_frame(f"jpeg{i}".encode(), ts=float(i))
    assert len(b._pending_frames) == 3
    assert b._pending_frames[0] == b"jpeg2"
    assert b._pending_frames[-1] == b"jpeg4"
    await b.close()
