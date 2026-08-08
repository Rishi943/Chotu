"""Convert an O/T/A session trace into brain-format SFT samples.

One sample per assistant turn: [system] + compacted text history + [STATE] +
frame tail (image paths) + the target assistant message. Reuses the brain's own
helpers (Scratchpad, maybe_compact, frame labels) so samples match the runtime
context byte-for-byte. Frames stay path references — base64 happens at training.

Usage:
    python -m scripts.robot.trace_to_brain out/sessions/<sid>_fable [-o out.jsonl]
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from core.loop_helpers import describe_motion, maybe_compact
from core.prompts import SYSTEM_PROMPT
from core.scratchpad import Scratchpad

# Mirrors core/brain.py COMPACT_AT_TOKENS / COMPACT_KEEP_TOKENS.
# Defaults read from env vars PALIV_COMPACT_AT_TOKENS / PALIV_COMPACT_KEEP_TOKENS;
# hard-coded defaults in brain.py are 10000 / 6000.
COMPACT_AT_TOKENS = 10000
COMPACT_KEEP_TOKENS = 6000
_FRAME_KEEP = 3
_MOTION_TOOLS = {"move", "pose"}


def _wrap_envelope(tool: str, result: dict) -> dict:
    """Skill-side wait results are bare {event,text,waited_s}; runtime wraps."""
    if "ok" in result:
        return result
    return {
        "ok": True, "tool": tool, "result": result,
        "duration_ms": int(float(result.get("waited_s") or 0) * 1000),
        "timestamp": 0, "error": None,
    }


def _clean_speak(env: dict) -> dict:
    env = copy.deepcopy(env)
    inner = env.get("result") or {}
    inner.pop("muted", None)
    inner.pop("played", None)
    env["result"] = inner
    return env


def _frame_msgs(frames: list[dict]) -> list[dict]:
    msgs = []
    n = len(frames)
    for i, fr in enumerate(frames):
        age = (n - 1) - i
        if age == 0:
            label = "[frame 0 | NOW — current view]"
        else:
            plural = "s" if age != 1 else ""
            motion = fr["motion"] or "no movement"
            label = f"[frame -{age} | {age} call{plural} ago, then: {motion}]"
        msgs.append({"role": "user", "content": [
            {"type": "image_path", "path": fr["path"]},
            {"type": "text", "text": label},
        ]})
    return msgs


def convert(session: Path) -> tuple[list[dict], list[dict]]:
    records = [json.loads(l) for l in (session / "trace.jsonl").open()]
    system = {"role": "system", "content": SYSTEM_PROMPT}
    body: list[dict] = []
    frames: list[dict] = []          # [{"path", "motion"}] — push_frame semantics
    samples: list[dict] = []
    markers: list[dict] = []
    pad = Scratchpad()
    thoughts: list[str] = []
    motion_since_frame = "no movement"

    for rec in records:
        if rec.get("thought"):
            thoughts.append(rec["thought"])
            continue
        payload = rec.get("action") or rec.get("observation")
        if payload is None:
            continue
        tool = payload["tool"]
        args = payload.get("args") or {}
        result = payload.get("result") or {}

        if tool == "marker":
            markers.append({"seq": rec["seq"], "text": args.get("text", "")})
            continue

        if tool == "capture_vision":
            if frames:
                frames[-1]["motion"] = motion_since_frame
            frames.append({"path": payload.get("frame", ""), "motion": ""})
            del frames[:-_FRAME_KEEP]
            motion_since_frame = "no movement"
            continue

        env = _wrap_envelope(tool, result)
        if tool == "speak":
            env = _clean_speak(env)

        # Snapshot scratchpad state and history window from BEFORE this turn.
        # The target assistant message appears exactly once — as the final message.
        state = pad.render()
        assistant = {
            "role": "assistant",
            "content": "\n".join(thoughts),
            "tool_calls": [{
                "id": f"call_{rec['seq']}", "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args)},
            }],
        }
        thoughts = []

        window = copy.deepcopy(body)
        maybe_compact(window, COMPACT_AT_TOKENS, COMPACT_KEEP_TOKENS)
        messages = [system] + window
        if state:
            messages.append({"role": "user", "content": state["content"]})
        messages += _frame_msgs(frames)
        messages.append(assistant)
        samples.append({"messages": messages})

        # Append completed turn to body AFTER emitting the sample so the result
        # never appears in the same sample's context.
        body.append(assistant)
        body.append({"role": "tool", "tool_call_id": f"call_{rec['seq']}",
                     "content": json.dumps(env)})
        inner = env.get("result") or {}
        if tool == "wait_for_event" and inner.get("event") in ("text", "speech") \
                and inner.get("text"):
            body.append({"role": "user", "content": f"[human] {inner['text']}"})

        if tool in _MOTION_TOOLS:
            motion_since_frame = describe_motion(tool, args)
        pad.update([(tool, args, env)])

    return samples, markers


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=None)
    ns = ap.parse_args()
    out = ns.out or Path("out/datasets") / (ns.session.name + ".jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    samples, markers = convert(ns.session)
    with out.open("w") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")
    out.with_suffix(".markers.json").write_text(json.dumps(markers, indent=2))
    print(f"{len(samples)} samples, {len(markers)} markers -> {out}")


if __name__ == "__main__":
    main()
