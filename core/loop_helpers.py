"""Pure helpers for the unified paced loop. No I/O, no globals — unit-testable."""

_MOTION_TOOLS = {"move", "pose"}


def describe_motion(name: str, args: dict) -> str:
    """Human phrase for the motion a tool call produced. Used to label frames."""
    if name == "move":
        direction = args.get("direction", "")
        steps = int(args.get("steps", 1) or 1)
        if direction in ("turn left", "turn right"):
            side = "left" if direction == "turn left" else "right"
            return f"turned {side} ~{steps * 30}°"
        if direction in ("forward", "backward"):
            unit = "step" if steps == 1 else "steps"
            return f"walked {direction} {steps} {unit}"
        return "moved"
    if name == "pose":
        return f"posed: {args.get('name', '?')}"
    return "no movement"


def motion_from_calls(calls: list[tuple[str, dict]]) -> str:
    """First motion tool's description, or 'no movement'. calls: [(name, args), ...]."""
    for name, args in calls:
        if name in _MOTION_TOOLS:
            return describe_motion(name, args)
    return "no movement"
