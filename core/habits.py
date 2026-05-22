"""Scripted IDLE habit implementations.

Each habit is an async coroutine: habit(pi: PiClient) -> None.
Brain calls run_habit(name, pi) to execute one.
IDLE_HABIT_MAP keys must stay in sync with picker.IDLE_HABITS.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

from core.pi_client import PiClient

logger = logging.getLogger(__name__)

HabitFn = Callable[[PiClient], Awaitable[None]]


# ---------------------------------------------------------------------------
# Habit implementations
# ---------------------------------------------------------------------------

async def _do_nothing(pi: PiClient) -> None:
    await asyncio.sleep(5)


async def _yawn(pi: PiClient) -> None:
    await pi.set_face("sleeping")
    await pi.pose(name="look up", speed=30)
    await asyncio.sleep(1.2)
    await pi.pose(name="stand", speed=30)
    await pi.set_face("idle")


async def _look_around(pi: PiClient) -> None:
    await pi.pose(name="look left", speed=40)
    await asyncio.sleep(0.8)
    await pi.pose(name="look right", speed=40)
    await asyncio.sleep(0.8)
    await pi.pose(name="stand", speed=40)


async def _pushup(pi: PiClient) -> None:
    await pi.do_trick(name="pushup", speed=60)


async def _twist(pi: PiClient) -> None:
    await pi.do_trick(name="twist", speed=60)


async def _swimming(pi: PiClient) -> None:
    await pi.do_trick(name="swimming", speed=60)


async def _handwork(pi: PiClient) -> None:
    await pi.do_trick(name="handwork", speed=60)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

IDLE_HABIT_MAP: dict[str, HabitFn] = {
    "do_nothing":  _do_nothing,
    "yawn":        _yawn,
    "look_around": _look_around,
    "pushup":      _pushup,
    "twist":       _twist,
    "swimming":    _swimming,
    "handwork":    _handwork,
}


async def run_habit(name: str, pi: PiClient) -> None:
    """Execute a named IDLE habit. Logs and swallows errors so brain loop never crashes."""
    fn = IDLE_HABIT_MAP.get(name)
    if fn is None:
        logger.warning("habits: unknown habit %r — skipping", name)
        return
    try:
        logger.info("habits: running %r", name)
        await fn(pi)
        logger.info("habits: %r complete", name)
    except Exception as e:
        logger.warning("habits: %r raised %s: %s", name, type(e).__name__, e)
