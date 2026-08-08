"""wait_for_event: the brain-side idle tool (mirrors the skill path's shape)."""

import asyncio
import sys
import time
import types

import pytest

from core.tools import local_wait_for_event


def _stub_brain(monkeypatch):
    stub = types.SimpleNamespace(
        pending_input=types.SimpleNamespace(arrived=asyncio.Event())
    )
    monkeypatch.setitem(sys.modules, "core.brain", stub)
    return stub


def test_timeout_event(monkeypatch):
    _stub_brain(monkeypatch)

    async def run():
        return await local_wait_for_event(timeout=1)

    env = asyncio.run(run())
    assert env["ok"] is True
    assert env["tool"] == "wait_for_event"
    assert env["result"]["event"] == "timeout"
    assert env["result"]["text"] is None
    assert env["result"]["waited_s"] >= 1.0


def test_text_event_wakes_early_and_does_not_drain(monkeypatch):
    stub = _stub_brain(monkeypatch)

    async def run():
        async def poke():
            await asyncio.sleep(0.1)
            stub.pending_input.arrived.set()

        task = asyncio.create_task(poke())
        env = await local_wait_for_event(timeout=30)
        await task
        return env

    start = time.time()
    env = asyncio.run(run())
    assert time.time() - start < 5
    assert env["result"]["event"] == "text"
    # buffer untouched: the loop drains it as the next [human] message
    assert stub.pending_input.arrived.is_set()


def test_timeout_clamped(monkeypatch):
    _stub_brain(monkeypatch)

    async def run():
        return await local_wait_for_event(timeout=0)

    env = asyncio.run(run())
    assert env["result"]["event"] == "timeout"
    assert 1.0 <= env["result"]["waited_s"] < 2.0


def test_schema_and_dispatch_renamed():
    from core.tools import TOOL_SCHEMAS

    names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert "wait_for_event" in names
    assert "wait" not in names
