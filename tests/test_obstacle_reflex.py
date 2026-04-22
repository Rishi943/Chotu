"""Tests for obstacle reflex — estop gate and poller behaviour."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from chotu.tools import _blocked_move, build_dispatch


# --- Helpers ---

def make_pi(distance_cm=None, ok=True):
    """Create a mock PiClient."""
    pi = MagicMock()
    if distance_cm is not None:
        pi.get_distance = AsyncMock(return_value={
            "ok": ok,
            "tool": "get_distance",
            "result": {"cm": distance_cm},
            "duration_ms": 5,
            "timestamp": time.time(),
            "error": None,
        })
    pi.move = AsyncMock(return_value={
        "ok": True, "tool": "move", "result": {}, "duration_ms": 100,
        "timestamp": time.time(), "error": None,
    })
    return pi


# --- _blocked_move ---

def test_blocked_move_returns_fake_success():
    result = _blocked_move()
    assert result["ok"] is True
    assert result["tool"] == "move"
    assert result["result"].get("blocked") is True
    assert result["error"] is None


# --- build_dispatch move gate ---

@pytest.mark.asyncio
async def test_move_passes_through_when_estop_clear():
    estop = asyncio.Event()  # not set
    pi = make_pi()
    dispatch = build_dispatch(pi, estop)
    result = await dispatch["move"](direction="forward", steps=1, speed=50)
    assert result["ok"] is True
    pi.move.assert_awaited_once_with(direction="forward", steps=1, speed=50)


@pytest.mark.asyncio
async def test_move_blocked_when_estop_set():
    estop = asyncio.Event()
    estop.set()
    pi = make_pi()
    dispatch = build_dispatch(pi, estop)
    result = await dispatch["move"](direction="forward", steps=1, speed=50)
    assert result["ok"] is True
    assert result["result"].get("blocked") is True
    pi.move.assert_not_awaited()


# --- obstacle_poller ---

@pytest.mark.asyncio
async def test_poller_sets_estop_when_close():
    from chotu.brain import obstacle_poller, OBSTACLE_CM
    estop = asyncio.Event()
    pi = make_pi(distance_cm=OBSTACLE_CM - 1)

    task = asyncio.create_task(obstacle_poller(pi, estop))
    await asyncio.sleep(0.05)  # let one poll fire
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert estop.is_set()


@pytest.mark.asyncio
async def test_poller_clears_estop_when_safe():
    from chotu.brain import obstacle_poller, OBSTACLE_CM
    estop = asyncio.Event()
    estop.set()  # start tripped
    pi = make_pi(distance_cm=OBSTACLE_CM + 5)

    task = asyncio.create_task(obstacle_poller(pi, estop))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert not estop.is_set()


@pytest.mark.asyncio
async def test_poller_ignores_failed_reads():
    from chotu.brain import obstacle_poller
    estop = asyncio.Event()
    pi = MagicMock()
    pi.get_distance = AsyncMock(return_value={
        "ok": False, "tool": "get_distance", "result": {},
        "duration_ms": 0, "timestamp": time.time(),
        "error": "pi_unreachable: connection refused",
    })

    task = asyncio.create_task(obstacle_poller(pi, estop))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert not estop.is_set()  # never tripped on error
