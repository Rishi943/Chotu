import asyncio
import pytest
from core.motion_lock import MotionLock
from core.async_motion import AsyncMotionRunner


class FakePending:
    def __init__(self):
        self.pushed = []
    def push(self, text):
        self.pushed.append(text)


@pytest.mark.asyncio
async def test_start_returns_started_ack_immediately():
    runner = AsyncMotionRunner(MotionLock(), FakePending())

    async def motion():
        await asyncio.sleep(0.05)
        return {"ok": True, "tool": "pose", "result": {}, "error": None}

    env = runner.start("pose", {"name": "push up"}, eta_ms=7000, coro_factory=motion)
    assert env["ok"] is True
    assert env["result"]["status"] == "started"
    assert runner.busy is True


@pytest.mark.asyncio
async def test_completion_releases_lock_and_pushes_event():
    pending = FakePending()
    runner = AsyncMotionRunner(MotionLock(), pending)

    async def motion():
        return {"ok": True, "tool": "pose", "result": {}, "error": None}

    runner.start("pose", {"name": "push up"}, eta_ms=7000, coro_factory=motion)
    await asyncio.sleep(0.01)  # let the task + done-callback run
    assert runner.busy is False
    assert any("motion_done" in m and "pose" in m for m in pending.pushed)


@pytest.mark.asyncio
async def test_second_motion_while_busy_is_rejected():
    runner = AsyncMotionRunner(MotionLock(), FakePending())

    async def slow():
        await asyncio.sleep(0.1)
        return {"ok": True, "tool": "pose", "result": {}, "error": None}

    runner.start("pose", {"name": "push up"}, eta_ms=7000, coro_factory=slow)
    rej = runner.start("move", {"direction": "forward"}, eta_ms=1500, coro_factory=slow)
    assert rej["ok"] is False
    assert "motion in progress" in rej["error"]


@pytest.mark.asyncio
async def test_motion_failure_reports_failed_event():
    pending = FakePending()
    runner = AsyncMotionRunner(MotionLock(), pending)

    async def failing():
        return {"ok": False, "tool": "pose", "result": {}, "error": "brownout"}

    runner.start("pose", {"name": "push up"}, eta_ms=7000, coro_factory=failing)
    await asyncio.sleep(0.01)
    assert any("failed" in m and "brownout" in m for m in pending.pushed)
