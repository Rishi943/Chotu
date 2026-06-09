"""Running world-state block injected into the volatile tail each turn.

Mechanical only: every field is derived from the loop's own tool calls/results —
no model parsing, no extra LLM call. Gives the model an explicit record of what it
just did and whether the distance sensor is trustworthy, so it stops re-deriving
state from near-identical frames (the "same rug, moving" repetition loop)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from core.loop_helpers import describe_motion

_DISTANCE_DEAD_AFTER = 3


def _heading_delta(name: str, args: dict) -> int:
    """Signed degrees a move call adds to the heading estimate. +right / -left."""
    if name != "move":
        return 0
    steps = int(args.get("steps", 1) or 1)
    direction = args.get("direction", "")
    if direction == "turn left":
        return -30 * steps
    if direction == "turn right":
        return 30 * steps
    return 0


@dataclass
class Scratchpad:
    """Mutable per-session working state. One instance lives in the loop."""

    heading: int = 0
    recent: deque = field(default_factory=lambda: deque(maxlen=3))
    distance_alive: bool = True
    distance_dead_streak: int = 0
    last_said: str = ""

    def update(self, calls: list[tuple[str, dict, dict]]) -> None:
        """Fold one turn's dispatched tools into state.
        `calls`: [(name, args, result_envelope), ...] for the tools run this turn."""
        for name, args, result in calls:
            if name in ("move", "pose"):
                self.recent.appendleft(describe_motion(name, args))
                self.heading += _heading_delta(name, args)
            elif name == "get_distance":
                reliable = bool(result.get("result", {}).get("reliable", False))
                if reliable:
                    self.distance_alive = True
                    self.distance_dead_streak = 0
                else:
                    self.distance_dead_streak += 1
                    if self.distance_dead_streak >= _DISTANCE_DEAD_AFTER:
                        self.distance_alive = False
            elif name == "speak":
                said = (args.get("text") or "").strip()
                if said:
                    self.last_said = said

    def render(self) -> dict | None:
        """One compact `[STATE]` user message for the volatile tail, or None when
        there is nothing worth saying yet. Caller must strip `_origin` before send."""
        lines: list[str] = []
        if self.recent:
            lines.append("recent actions (newest first): " + " · ".join(self.recent))
            lines.append(f"heading: ~{self.heading}° from start")
        if not self.distance_alive:
            lines.append("sensors: distance = DEAD (unreliable, ignore it)")
        if self.last_said:
            lines.append(f'last said: "{self.last_said}"')
        if not lines:
            return None
        return {"role": "user", "content": "[STATE]\n" + "\n".join(lines), "_origin": "state"}
