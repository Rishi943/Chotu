"""(Observation, Thought, Action) trace logger. One folder per session under
out/sessions/, written by the tool bridge on every run path. Frugal: appends
JSONL + saves frames to disk; no images embedded in the JSON."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

_OBS = {"capture_vision", "get_distance", "get_battery", "get_perception", "health", "state", "marker"}
_ACT = {"move", "pose", "set_legs", "peek_over", "speak", "set_face", "set_light", "play_sequence"}


def _out_root() -> Path:
    return Path(os.getenv("PALIV_OUT", "out"))


def session_dir(runner: str = "fable") -> Path:
    """Current session folder. Stable within a session via PALIV_TRACE_DIR
    (exported by the first caller so sibling subprocesses share it)."""
    env = os.getenv("PALIV_TRACE_DIR")
    if env:
        d = Path(env)
    else:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        d = _out_root() / "sessions" / f"{ts}_{runner}"
    (d / "frames").mkdir(parents=True, exist_ok=True)
    return d


def _next_seq(d: Path) -> int:
    f = d / "trace.jsonl"
    if not f.exists():
        return 0
    return sum(1 for _ in f.open())


def classify(tool: str) -> str:
    if tool in _OBS:
        return "observation"
    if tool in _ACT:
        return "action"
    return "action"  # default unknown tools to action


def record(kind: str, tool: str, args: dict, result: dict,
           *, frame: str | None = None, thought: str | None = None) -> None:
    d = session_dir()
    rec = {
        "ts": time.time(),
        "seq": _next_seq(d),
        "observation": None,
        "thought": thought,
        "action": None,
    }
    payload = {"tool": tool, "args": args, "result": result}
    if frame:
        payload["frame"] = frame
    if kind == "observation":
        rec["observation"] = payload
    elif kind == "action":
        rec["action"] = payload
    with (d / "trace.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")


def save_frame(jpeg_bytes: bytes) -> str:
    d = session_dir()
    idx = len(list((d / "frames").glob("*.jpg")))
    rel = f"frames/{idx:03d}.jpg"
    (d / rel).write_bytes(jpeg_bytes)
    return rel
