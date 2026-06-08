"""Event text injection — events fold into the loop's pending-input buffer.

There is no longer a priority queue or tool-chain guard: the paced loop drains
pending input every iteration, so events always land on the next call.
"""

from core.brain import pending_input


def inject_event(subkind: str, payload: str = "") -> None:
    body = f"[event] {subkind}" + (f": {payload}" if payload else "")
    pending_input.push(body)
