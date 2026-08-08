"""One slot. Never a queue. Cleared by a new utterance."""
import asyncio
import pytest
from core.motion_lock import MotionLock
from core.async_motion import AsyncMotionRunner
from core.loop_helpers import PendingInput


def make():
    lock, pending = MotionLock(), PendingInput()
    return AsyncMotionRunner(lock, pending), pending


async def test_a_staged_move_fires_when_the_first_one_finishes():
    runner, _ = make()
    fired = asyncio.Event()

    async def first():
        await asyncio.sleep(0.02)
        return {"ok": True}

    async def second():
        fired.set()
        return {"ok": True}

    runner.start("move", {"direction": "forward"}, 20, first)
    assert runner.stage("act", {"name": "wave"}, 20, second) is True
    await asyncio.wait_for(fired.wait(), timeout=1.0)


async def test_staging_twice_replaces_rather_than_queues():
    runner, _ = make()
    order = []

    async def slow():
        await asyncio.sleep(0.02)
        return {"ok": True}

    async def a():
        order.append("a")
        return {"ok": True}

    async def b():
        order.append("b")
        return {"ok": True}

    runner.start("move", {}, 20, slow)
    runner.stage("act", {"name": "wave"}, 20, a)
    runner.stage("act", {"name": "sit"}, 20, b)
    await asyncio.sleep(0.1)
    assert order == ["b"], "only the newest staged move may run"


async def test_clear_stage_cancels_it():
    runner, _ = make()
    fired = []

    async def slow():
        await asyncio.sleep(0.02)
        return {"ok": True}

    async def never():
        fired.append(1)
        return {"ok": True}

    runner.start("move", {}, 20, slow)
    runner.stage("act", {"name": "wave"}, 20, never)
    runner.clear_stage()
    await asyncio.sleep(0.1)
    assert fired == []


async def test_staging_with_nothing_running_is_refused():
    runner, _ = make()

    async def anything():
        return {"ok": True}

    assert runner.stage("act", {"name": "wave"}, 20, anything) is False


async def test_motion_done_still_reaches_pending_input():
    runner, pending = make()

    async def quick():
        return {"ok": True}

    runner.start("move", {}, 20, quick)
    await asyncio.sleep(0.05)
    assert any("motion_done" in m for m in pending.drain().splitlines())
