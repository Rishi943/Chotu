"""Tests for obstacle reflex — estop gate and poller behaviour."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from chotu.tools import build_dispatch


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


# --- build_dispatch move gate ---

@pytest.mark.asyncio
async def test_move_passes_through_when_estop_clear():
    """When estop is not set, move should pass through to Pi."""
    estop = asyncio.Event()  # not set
    pi = make_pi()
    dispatch = build_dispatch(pi, estop)
    result = await dispatch["move"](direction="forward", steps=1, speed=50)
    assert result["ok"] is True
    assert result["tool"] == "move"
    assert result["result"].get("blocked") is not True
    pi.move.assert_awaited_once_with(direction="forward", steps=1, speed=50)


@pytest.mark.asyncio
async def test_move_blocked_when_estop_set():
    """When estop is set, move should return blocked envelope without calling Pi."""
    estop = asyncio.Event()
    estop.set()
    pi = make_pi()
    dispatch = build_dispatch(pi, estop)
    result = await dispatch["move"](direction="forward", steps=1, speed=50)
    assert result["ok"] is True
    assert result["tool"] == "move"
    assert result["result"].get("blocked") is True
    assert result["error"] is None
    pi.move.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_legs_passes_through_when_estop_clear():
    """When estop is not set, set_legs should pass through to Pi."""
    estop = asyncio.Event()  # not set
    pi = MagicMock()
    pi.set_legs = AsyncMock(return_value={
        "ok": True, "tool": "set_legs", "result": {}, "duration_ms": 100,
        "timestamp": time.time(), "error": None,
    })
    dispatch = build_dispatch(pi, estop)
    legs = [[60, 0, -30], [60, 0, -30], [60, 0, -30], [60, 0, -30]]
    result = await dispatch["set_legs"](legs=legs, speed=80)
    assert result["ok"] is True
    assert result["result"].get("blocked") is not True
    pi.set_legs.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_legs_blocked_when_estop_set():
    """When estop is set, set_legs should return blocked envelope."""
    estop = asyncio.Event()
    estop.set()
    pi = MagicMock()
    pi.set_legs = AsyncMock()
    dispatch = build_dispatch(pi, estop)
    legs = [[60, 0, -30], [60, 0, -30], [60, 0, -30], [60, 0, -30]]
    result = await dispatch["set_legs"](legs=legs, speed=80)
    assert result["ok"] is True
    assert result["tool"] == "set_legs"
    assert result["result"].get("blocked") is True
    pi.set_legs.assert_not_awaited()


# --- obstacle_poller ---

@pytest.mark.asyncio
async def test_poller_sets_estop_when_close():
    """When distance is below OBSTACLE_CM, estop should be set."""
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
    """When distance is above OBSTACLE_CM, estop should be cleared."""
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
    """When distance read fails, estop should not be modified."""
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
