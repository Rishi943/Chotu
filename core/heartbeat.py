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
