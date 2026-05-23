"""Unit tests for heartbeat scheduler and tool-chain guard."""

import asyncio
import pytest


@pytest.mark.asyncio
async def test_tool_chain_active_blocks_heartbeat():
    from core.heartbeat import should_fire_heartbeat
    active = asyncio.Event()

    active.clear()
    assert should_fire_heartbeat(active, bypass=False) is True

    active.set()
    assert should_fire_heartbeat(active, bypass=False) is False

    # Hard interrupts bypass the guard
    assert should_fire_heartbeat(active, bypass=True) is True


@pytest.mark.asyncio
async def test_tagged_input_shape():
    from core.brain import wrap_user_input, wrap_heartbeat, wrap_event
    assert wrap_user_input("hi") == {"kind": "user", "text": "hi"}
    assert wrap_heartbeat() == {"kind": "heartbeat", "text": "[heartbeat]"}
    assert wrap_event("wake_word", "hello") == {
        "kind": "event", "subkind": "wake_word", "text": "[event] wake_word: hello"
    }


@pytest.mark.asyncio
async def test_heartbeat_loop_fires_when_idle():
    from core.heartbeat import heartbeat_loop
    queue: asyncio.Queue = asyncio.Queue()
    active = asyncio.Event()  # idle

    task = asyncio.create_task(heartbeat_loop(queue, active, interval=0.05))
    await asyncio.sleep(0.18)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass

    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    assert len(items) >= 2, f"expected >=2 heartbeats in 0.18s @ 0.05s, got {len(items)}"
    assert all(i["kind"] == "heartbeat" for i in items)


@pytest.mark.asyncio
async def test_heartbeat_loop_skips_when_active():
    from core.heartbeat import heartbeat_loop
    queue: asyncio.Queue = asyncio.Queue()
    active = asyncio.Event()
    active.set()  # tool chain active — skip everything

    task = asyncio.create_task(heartbeat_loop(queue, active, interval=0.05))
    await asyncio.sleep(0.18)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass

    assert queue.empty(), "heartbeat fired while tool chain was active"


@pytest.mark.asyncio
async def test_inject_event_wake_word_respects_guard():
    from core.events import inject_event
    queue: asyncio.Queue = asyncio.Queue()
    active = asyncio.Event(); active.set()  # busy

    inject_event(queue, active, "wake_word", payload="hey chotu")
    assert queue.empty(), "wake_word fired while tool chain active"

    active.clear()
    inject_event(queue, active, "wake_word", payload="hey chotu")
    item = queue.get_nowait()
    assert item["kind"] == "event"
    assert item["subkind"] == "wake_word"
    assert "hey chotu" in item["text"]


@pytest.mark.asyncio
async def test_inject_event_battery_low_bypasses_guard():
    from core.events import inject_event
    queue: asyncio.Queue = asyncio.Queue()
    active = asyncio.Event(); active.set()  # busy

    inject_event(queue, active, "battery_low", payload="14%")
    item = queue.get_nowait()
    assert item["subkind"] == "battery_low"
    assert "14%" in item["text"]


@pytest.mark.asyncio
async def test_inject_event_stop_word_bypasses_guard():
    from core.events import inject_event
    queue: asyncio.Queue = asyncio.Queue()
    active = asyncio.Event(); active.set()

    inject_event(queue, active, "stop_word")
    item = queue.get_nowait()
    assert item["subkind"] == "stop_word"
