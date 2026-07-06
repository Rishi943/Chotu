import asyncio
import pytest
from core.tools import build_dispatch, dispatch_tool
from core.motion_lock import MotionLock
from core.async_motion import AsyncMotionRunner


class FakePi:
    def __init__(self):
        self.pose_calls = 0
    async def pose(self, **kw):
        self.pose_calls += 1
        await asyncio.sleep(0.05)
        return {"ok": True, "tool": "pose", "result": {"pose": kw.get("name")}, "error": None}


class FakePending:
    def __init__(self):
        self.pushed = []
    def push(self, text):
        self.pushed.append(text)


@pytest.mark.asyncio
async def test_pose_dispatch_returns_started_not_blocking():
    pi = FakePi()
    runner = AsyncMotionRunner(MotionLock(), FakePending())
    estop = asyncio.Event()
    dmap = build_dispatch(pi, estop, motion_runner=runner)
    env = await dispatch_tool(dmap, "pose", '{"name": "push up"}')
    assert env["result"]["status"] == "started"   # returned before the 0.05s motion finished
    assert runner.busy is True
