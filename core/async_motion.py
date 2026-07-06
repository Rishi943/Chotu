"""Fire-and-forget motion runner.

A motion tool no longer blocks the model's turn on the ~8s servo run. `start`
acquires the MotionLock, launches the Pi call as a task, and returns a `started`
ack immediately. When the task finishes, a done-callback releases the lock and
injects `[event] motion_done: …` so completion arrives as an observation on a
later turn. A second motion while one is active is refused (MotionLock rejection).
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional


class AsyncMotionRunner:
    def __init__(self, motion_lock, pending_input) -> None:
        self._lock = motion_lock
        self._pending = pending_input
        self._task: Optional[asyncio.Task] = None
        self._current: Optional[tuple[str, dict]] = None

    @property
    def busy(self) -> bool:
        return self._lock.active is not None

    def start(
        self,
        tool: str,
        args: dict,
        eta_ms: int,
        coro_factory: Callable[[], Awaitable[dict]],
    ) -> dict:
        if not self._lock.acquire_now(tool, args, eta_ms):
            return self._lock.rejection_envelope(tool)
        self._current = (tool, dict(args))
        self._task = asyncio.create_task(coro_factory())
        self._task.add_done_callback(self._on_done)
        return {
            "ok": True,
            "tool": tool,
            "result": {"status": "started", "eta_ms": int(eta_ms)},
            "duration_ms": 0,
            "timestamp": time.time(),
            "error": None,
        }

    def _on_done(self, task: asyncio.Task) -> None:
        tool = (self._current or ("?", {}))[0]
        self._lock.release()
        self._current = None
        self._task = None
        try:
            env = task.result()
        except Exception as e:  # motion coro raised
            env = {"ok": False, "error": str(e)}
        if env.get("ok"):
            detail = "done"
        else:
            detail = f"failed: {env.get('error')}"
        self._pending.push(f"[event] motion_done: {tool} {detail}")
