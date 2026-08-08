"""Routing, path safety, and the envelope shape."""
import json
import pathlib
import pytest
from core.dispatch import build_dispatch, dispatch_tool


class FakePi:
    def __init__(self):
        self.calls = []

    async def _ok(self, tool, result):
        self.calls.append(tool)
        return {"ok": True, "tool": tool, "result": result,
                "duration_ms": 1, "timestamp": 0.0, "error": None}

    async def pose(self, name, speed=50):
        return await self._ok("pose", {"pose": name})

    async def do_trick(self, name, speed=80):
        return await self._ok("trick", {"name": name})

    async def move(self, direction, steps=1, speed=70):
        return await self._ok("move", {"direction": direction, "steps": steps})

    async def get_battery(self):
        return await self._ok("battery", {"percent": 62, "voltage": 7.4})

    async def get_distance(self):
        return await self._ok("distance", {"cm": 40.0, "reliable": True})

    async def capture(self, full=False):
        return await self._ok("capture", {"image_base64": "AAA"})


class FakeRunner:
    """Stands in for AsyncMotionRunner: acks instantly, records the coro."""
    def __init__(self):
        self.started = []

    def start(self, tool, args, eta_ms, coro_factory):
        self.started.append((tool, args))
        return {"ok": True, "tool": tool,
                "result": {"status": "started", "eta_ms": eta_ms},
                "duration_ms": 0, "timestamp": 0.0, "error": None}


class FakeSpeaker:
    def __init__(self):
        self.said = []

    async def speak(self, text):
        self.said.append(text)
        return {"ok": True, "tool": "say", "result": {"text": text},
                "duration_ms": 0, "timestamp": 0.0, "error": None}


@pytest.fixture
def kit():
    pi, runner, speaker = FakePi(), FakeRunner(), FakeSpeaker()
    return pi, runner, speaker, build_dispatch(pi, runner, speaker)


@pytest.mark.asyncio
async def test_act_push_up_reaches_the_trick(kit):
    _, runner, _, d = kit
    env = await dispatch_tool(d, "act", json.dumps({"name": "push up"}))
    assert env["ok"]
    assert runner.started == [("act", {"name": "push up"})]


@pytest.mark.asyncio
async def test_act_rejects_an_unknown_name(kit):
    _, _, _, d = kit
    env = await dispatch_tool(d, "act", json.dumps({"name": "backflip"}))
    assert env["ok"] is False
    assert "backflip" in env["error"]


@pytest.mark.asyncio
async def test_sense_battery(kit):
    _, _, _, d = kit
    env = await dispatch_tool(d, "sense", json.dumps({"what": "battery"}))
    assert env["ok"] and env["result"]["percent"] == 62


@pytest.mark.asyncio
async def test_sense_rejects_unknown_kind(kit):
    _, _, _, d = kit
    env = await dispatch_tool(d, "sense", json.dumps({"what": "temperature"}))
    assert env["ok"] is False


@pytest.mark.asyncio
async def test_say_goes_to_the_speaker(kit):
    _, _, speaker, d = kit
    await dispatch_tool(d, "say", json.dumps({"text": "hello uncle"}))
    assert speaker.said == ["hello uncle"]


@pytest.mark.asyncio
async def test_read_stays_inside_docs(kit):
    _, _, _, d = kit
    env = await dispatch_tool(d, "read", json.dumps({"path": "../CHOTU.md"}))
    assert env["ok"] is False
    assert "docs/" in env["error"]


@pytest.mark.asyncio
async def test_read_missing_file_is_an_error_not_a_crash(kit):
    _, _, _, d = kit
    env = await dispatch_tool(d, "read", json.dumps({"path": "nope.md"}))
    assert env["ok"] is False


@pytest.mark.asyncio
async def test_unknown_tool_is_an_envelope_not_an_exception(kit):
    _, _, _, d = kit
    env = await dispatch_tool(d, "teleport", "{}")
    assert env["ok"] is False


@pytest.mark.asyncio
async def test_malformed_json_is_an_envelope(kit):
    _, _, _, d = kit
    env = await dispatch_tool(d, "move", "{not json")
    assert env["ok"] is False
