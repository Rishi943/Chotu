"""Pure helpers for the unified paced loop. No I/O, no globals — unit-testable."""

import asyncio

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


def trim_loop_window(memory: list[dict], n: int) -> None:
    """Keep the last `n` iterations (each starting at an assistant message) plus their
    trailing tool results. Drop everything older. Mutates `memory`."""
    starts = [i for i, m in enumerate(memory) if m.get("role") == "assistant"]
    if len(starts) <= n:
        return
    cut = starts[len(starts) - n]
    del memory[:cut]


def strip_old_monologue(memory: list[dict], keep_last: int = 2) -> None:
    """Blank the `content` of all but the last `keep_last` assistant messages.
    Tool calls are preserved. Mutates `memory`."""
    a_idxs = [i for i, m in enumerate(memory) if m.get("role") == "assistant"]
    targets = a_idxs if keep_last == 0 else a_idxs[:-keep_last]
    for i in targets:
        if memory[i].get("content"):
            memory[i] = {**memory[i], "content": ""}


def pace_remainder(tool_duration: float, floor: float) -> float:
    """Extra seconds to sleep after an iteration so the gap is at least `floor`."""
    return max(0.0, floor - tool_duration)


def maybe_compact(memory: list[dict], compact_at: int, keep: int) -> None:
    """Append-only between compactions: leave `memory` untouched until it holds
    `compact_at` assistant turns, then trim to the last `keep`. Trimming is the
    only moment the cached prefix changes, so it happens rarely instead of every
    iteration. The running state block (scratchpad) carries continuity across the
    cut, so no summary is needed. Mutates `memory`."""
    n_turns = sum(1 for m in memory if m.get("role") == "assistant")
    if n_turns >= compact_at:
        trim_loop_window(memory, keep)


def cap_result(text: str, max_chars: int = 1500) -> str:
    """Cap an oversized tool-result string (head-keep + marker). Tiny envelopes
    pass through untouched; guards against a fat capture_vision/perception payload
    silently bloating every cached call until the next compaction."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n…[truncated {len(text) - max_chars} chars]"


def split_tool_calls(tool_calls):
    """Dedupe a call list by class: at most one motion (move/pose) and one speak.
    Returns (keep, suppressed). Order preserved; first of each class wins."""
    keep, suppressed = [], []
    seen_motion = seen_speak = False
    for tc in tool_calls:
        name = tc.function.name
        if name in _MOTION_TOOLS:
            if seen_motion:
                suppressed.append(tc); continue
            seen_motion = True
        elif name == "speak":
            if seen_speak:
                suppressed.append(tc); continue
            seen_speak = True
        keep.append(tc)
    return keep, suppressed


class PendingInput:
    """Single-buffer replacement for the priority queue. Terminal/GUI/voice/events
    push text; the loop drains it once per iteration. `arrived` lets the pace-sleep
    wake early when input shows up."""

    def __init__(self):
        self._buf: list[str] = []
        self.arrived = asyncio.Event()

    def push(self, text: str) -> None:
        t = (text or "").strip()
        if t:
            self._buf.append(t)
            self.arrived.set()

    def drain(self) -> str | None:
        if not self._buf:
            return None
        joined = "\n".join(self._buf)
        self._buf.clear()
        self.arrived.clear()
        return joined


async def paced_sleep(remainder: float, pending: "PendingInput") -> None:
    """Sleep up to `remainder` seconds, returning early if pending input arrives."""
    if remainder <= 0:
        return
    try:
        await asyncio.wait_for(pending.arrived.wait(), timeout=remainder)
    except asyncio.TimeoutError:
        pass
