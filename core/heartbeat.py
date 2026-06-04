"""Heartbeat scheduler — periodic synthetic ticks for chotu's brain loop.

Modelled on OpenClaw's heartbeat: a scheduled agent turn that runs in the
same session/context. Skipped if a tool chain is currently active.
"""

import asyncio
import os


HEARTBEAT_INTERVAL = int(os.getenv("PALIV_HEARTBEAT_INTERVAL", "2"))


def should_fire_heartbeat(tool_chain_active: asyncio.Event, *, bypass: bool = False) -> bool:
    """Return True iff a heartbeat may fire right now."""
    if bypass:
        return True
    return not tool_chain_active.is_set()


async def heartbeat_loop(input_queue: asyncio.Queue, tool_chain_active: asyncio.Event,
                        interval: float | int | None = None) -> None:
    """Fire a heartbeat when BOTH conditions hold:
      (a) MIN_INTERVAL has elapsed since the last heartbeat fired
      (b) no tool chain is currently active

    next_fire = max(last_fire + MIN_INTERVAL, completion_time)
    Fast turns wait out the timer. Slow turns (e.g. a move) fire immediately on completion.
    """
    iv = float(interval if interval is not None else HEARTBEAT_INTERVAL)
    loop = asyncio.get_event_loop()
    last_fire = loop.time()

    while True:
        # Wait until MIN_INTERVAL has elapsed since last fire
        remaining = (last_fire + iv) - loop.time()
        if remaining > 0:
            await asyncio.sleep(remaining)

        # Wait for any active tool chain to finish
        while tool_chain_active.is_set():
            await asyncio.sleep(0.05)

        # Fire
        last_fire = loop.time()
        from core.brain import wrap_heartbeat
        try:
            input_queue.put_nowait(wrap_heartbeat())
        except asyncio.QueueFull:
            pass
