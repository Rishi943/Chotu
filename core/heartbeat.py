"""Heartbeat scheduler — periodic synthetic ticks for chotu's brain loop.

Modelled on OpenClaw's heartbeat: a scheduled agent turn that runs in the
same session/context. Skipped if a tool chain is currently active.
"""

import asyncio
import os


HEARTBEAT_INTERVAL = int(os.getenv("PALIV_HEARTBEAT_INTERVAL", "10"))


def should_fire_heartbeat(tool_chain_active: asyncio.Event, *, bypass: bool = False) -> bool:
    """Return True iff a heartbeat may fire right now."""
    if bypass:
        return True
    return not tool_chain_active.is_set()


async def heartbeat_loop(input_queue: asyncio.Queue, tool_chain_active: asyncio.Event,
                        interval: float | int | None = None) -> None:
    """Inject a heartbeat synthetic message every `interval` seconds when idle.

    Ticks are skipped (not queued) while a tool chain is active.
    """
    iv = float(interval if interval is not None else HEARTBEAT_INTERVAL)
    while True:
        await asyncio.sleep(iv)
        if should_fire_heartbeat(tool_chain_active):
            from core.brain import wrap_heartbeat
            try:
                input_queue.put_nowait(wrap_heartbeat())
            except asyncio.QueueFull:
                pass
