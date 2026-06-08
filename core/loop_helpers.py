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


def push_frame(stack: list[dict], image_b64: str, motion: str, keep: int = 3) -> None:
    """Append a new current frame. The transition `motion` that produced it is
    recorded on the previously-current frame. Trims to the newest `keep`. Mutates `stack`."""
    if stack:
        stack[-1]["motion"] = motion
    stack.append({"image_b64": image_b64, "motion": ""})
    if len(stack) > keep:
        del stack[: len(stack) - keep]


def render_frames(stack: list[dict]) -> list[dict]:
    """Render the frame stack as multimodal user messages, oldest first. The newest
    is labeled NOW; older ones carry recency + the motion taken right after them."""
    msgs = []
    n = len(stack)
    for i, fr in enumerate(stack):
        age = (n - 1) - i
        if age == 0:
            label = "[frame 0 | NOW — current view]"
        else:
            plural = "s" if age != 1 else ""
            motion = fr["motion"] or "no movement"
            label = f"[frame -{age} | {age} call{plural} ago, then: {motion}]"
        msgs.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{fr['image_b64']}"}},
                {"type": "text", "text": label},
            ],
        })
    return msgs
