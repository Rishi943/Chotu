"""Event injectors — push tagged event items into the brain's input queue.

wake_word respects the tool-chain guard (skipped if busy).
battery_low and stop_word are hard interrupts; they bypass the guard.
"""

import asyncio


HARD_INTERRUPT_SUBKINDS = frozenset({"battery_low", "stop_word"})


def inject_event(input_queue: asyncio.Queue, tool_chain_active: asyncio.Event,
                 subkind: str, payload: str = "") -> bool:
    """Push an event item. Returns True if pushed, False if suppressed by the guard."""
    bypass = subkind in HARD_INTERRUPT_SUBKINDS
    if not bypass and tool_chain_active.is_set():
        return False

    from core.brain import wrap_event
    try:
        input_queue.put_nowait(wrap_event(subkind, payload))
        return True
    except asyncio.QueueFull:
        return False
