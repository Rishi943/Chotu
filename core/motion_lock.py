"""Single-motion-at-a-time lock.

Motion tools: move, turn, set_legs, pose, trick. Only one runs at a time.
Attempts to start a second motion while one is held are REJECTED — never
queued — with a dict envelope shaped like a Pi error response. The model
sees the rejection in its tool-result stream and can replan in-context.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Optional


MOTION_TOOLS = frozenset({"move", "turn", "set_legs", "pose", "trick"})

REJECTED_ENVELOPE_KEYS = ("ok", "tool", "result", "duration_ms", "timestamp", "error")


class MotionLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active: Optional[dict] = None

    @property
    def active(self) -> Optional[dict]:
        """Currently-running motion metadata, or None when free."""
        return dict(self._active) if self._active else None

    def remaining_ms(self) -> int:
        """Estimated milliseconds remaining on the active motion. 0 when free."""
        if not self._active:
            return 0
        elapsed = (time.monotonic() - self._active["started_at"]) * 1000.0
        return max(0, int(self._active["eta_ms"] - elapsed))

    def try_acquire(self, tool: str, args: dict, eta_ms: int) -> Optional[dict]:
        """Non-blocking probe. Returns None if caller may proceed, or a rejection
        envelope dict to return to the model. Does NOT actually acquire the lock —
        use `acquire()` for that."""
        if self._lock.locked():
            return self._rejection_envelope(tool)
        return None

    @asynccontextmanager
    async def acquire(self, tool: str, args: dict, eta_ms: int):
        """Async context manager. Yields True on acquire, False if already held
        (the caller should fall back to the rejection envelope from try_acquire)."""
        if self._lock.locked():
            yield False
            return
        await self._lock.acquire()
        self._active = {
            "tool": tool,
            "args": dict(args),
            "started_at": time.monotonic(),
            "eta_ms": int(eta_ms),
        }
        try:
            yield True
        finally:
            self._active = None
            self._lock.release()

    def _rejection_envelope(self, attempted_tool: str) -> dict:
        a = self._active or {}
        remaining_s = self.remaining_ms() / 1000.0
        active_tool = a.get("tool", "?")
        active_args = a.get("args", {})
        arg_hint = ""
        if active_tool == "trick" and "name" in active_args:
            arg_hint = f"({active_args['name']})"
        elif active_tool == "move" and "direction" in active_args:
            arg_hint = f"({active_args['direction']})"
        return {
            "ok": False,
            "tool": attempted_tool,
            "result": {},
            "duration_ms": 0,
            "timestamp": time.time(),
            "error": f"motion in progress: {active_tool}{arg_hint}, ~{remaining_s:.1f}s remaining",
        }
