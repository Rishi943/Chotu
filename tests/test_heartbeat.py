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
