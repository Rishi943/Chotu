"""MotionLock: at most one motion tool in flight at a time. New attempts while
held are REJECTED (not queued) with an informative envelope so the model sees
it and replans."""

import asyncio
import time

import pytest

from core.motion_lock import MotionLock, REJECTED_ENVELOPE_KEYS


async def test_acquire_when_free():
    lock = MotionLock()
    async with lock.acquire("move", {"direction": "forward"}, eta_ms=3000) as ok:
        assert ok is True


async def test_reject_when_held_returns_envelope():
    lock = MotionLock()
    async with lock.acquire("trick", {"name": "pushup"}, eta_ms=6000):
        rejection = lock.try_acquire("move", {"direction": "forward"}, eta_ms=3000)
        assert rejection is not None
        for key in REJECTED_ENVELOPE_KEYS:
            assert key in rejection
        assert rejection["ok"] is False
        assert "motion in progress" in rejection["error"]
        assert "trick" in rejection["error"]


async def test_released_after_context_exit():
    lock = MotionLock()
    async with lock.acquire("move", {}, eta_ms=100):
        pass
    rejection = lock.try_acquire("turn", {}, eta_ms=100)
    assert rejection is None


async def test_metadata_reports_active_tool():
    lock = MotionLock()
    assert lock.active is None
    async with lock.acquire("trick", {"name": "wave"}, eta_ms=4000):
        active = lock.active
        assert active is not None
        assert active["tool"] == "trick"
        assert active["args"] == {"name": "wave"}
        assert active["eta_ms"] == 4000
        assert isinstance(active["started_at"], float)
    assert lock.active is None


async def test_remaining_ms_decreases():
    lock = MotionLock()
    async with lock.acquire("move", {}, eta_ms=200):
        first = lock.remaining_ms()
        await asyncio.sleep(0.05)
        second = lock.remaining_ms()
        assert second < first
