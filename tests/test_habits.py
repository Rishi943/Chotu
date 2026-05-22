"""Dry unit tests for core.habits — no Pi required."""

import asyncio
from unittest.mock import patch

import pytest

from core.habits import IDLE_HABIT_MAP, run_habit


class _MockPi:
    """Records all Pi calls. Never raises."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def set_face(self, name: str) -> dict:
        self.calls.append(("set_face", name))
        return {"ok": True}

    async def pose(self, name: str, speed: int = 50) -> dict:
        self.calls.append(("pose", name, speed))
        return {"ok": True}

    async def do_trick(self, name: str, speed: int = 70) -> dict:
        self.calls.append(("do_trick", name, speed))
        return {"ok": True}


class _BrokenPi:
    """Every call raises — used to verify run_habit never propagates."""

    async def set_face(self, **kw):
        raise ConnectionError("Pi unreachable")

    async def pose(self, **kw):
        raise ConnectionError("Pi unreachable")

    async def do_trick(self, **kw):
        raise ConnectionError("Pi unreachable")


# ---------------------------------------------------------------------------
# Map completeness
# ---------------------------------------------------------------------------

def test_idle_habit_map_matches_picker():
    from core.picker import IDLE_HABITS
    assert set(IDLE_HABIT_MAP.keys()) == set(IDLE_HABITS)


# ---------------------------------------------------------------------------
# do_nothing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_do_nothing_makes_no_pi_calls():
    pi = _MockPi()
    with patch("asyncio.sleep"):
        await run_habit("do_nothing", pi)
    assert pi.calls == []


# ---------------------------------------------------------------------------
# yawn
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_yawn_call_sequence():
    pi = _MockPi()
    with patch("asyncio.sleep"):
        await run_habit("yawn", pi)
    assert pi.calls == [
        ("set_face", "sleeping"),
        ("pose", "look up", 30),
        ("pose", "stand", 30),
        ("set_face", "idle"),
    ]


# ---------------------------------------------------------------------------
# look_around
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_look_around_call_sequence():
    pi = _MockPi()
    with patch("asyncio.sleep"):
        await run_habit("look_around", pi)
    assert pi.calls == [
        ("pose", "look left", 40),
        ("pose", "look right", 40),
        ("pose", "stand", 40),
    ]


# ---------------------------------------------------------------------------
# tricks — one Pi call each
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pushup_calls_trick():
    pi = _MockPi()
    await run_habit("pushup", pi)
    assert pi.calls == [("do_trick", "pushup", 60)]


@pytest.mark.asyncio
async def test_twist_calls_trick():
    pi = _MockPi()
    await run_habit("twist", pi)
    assert pi.calls == [("do_trick", "twist", 60)]


@pytest.mark.asyncio
async def test_swimming_calls_trick():
    pi = _MockPi()
    await run_habit("swimming", pi)
    assert pi.calls == [("do_trick", "swimming", 60)]


@pytest.mark.asyncio
async def test_handwork_calls_trick():
    pi = _MockPi()
    await run_habit("handwork", pi)
    assert pi.calls == [("do_trick", "handwork", 60)]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_habit_is_noop():
    pi = _MockPi()
    await run_habit("moonwalk", pi)  # must not raise
    assert pi.calls == []


@pytest.mark.asyncio
async def test_pi_error_does_not_propagate_for_sequence_habit():
    # yawn calls set_face + pose — if Pi is broken, run_habit must still return cleanly
    await run_habit("yawn", _BrokenPi())  # must not raise


@pytest.mark.asyncio
async def test_pi_error_does_not_propagate_for_trick_habit():
    await run_habit("pushup", _BrokenPi())  # must not raise
