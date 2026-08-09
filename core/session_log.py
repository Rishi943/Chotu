"""Console event session log. Appends every console event to a JSONL file on
disk so a conversation can be read back after the browser tab is closed.

Behaviour is deliberately silent: log_event never raises — a logging fault
must not take down the robot.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

_cache_path: Path | None = None
_seq = 0


def _out_root() -> Path:
    return Path(os.getenv("PALIV_OUT", "out"))


def session_path() -> Path:
    """Log file for this process. The timestamp is fixed the first time this is
    called and reused for every later call (one file per run, not per event)."""
    global _cache_path
    if _cache_path is not None:
        return _cache_path
    env = os.getenv("PALIV_SESSION_DIR")
    if env:
        d = Path(env)
    else:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        d = _out_root() / "sessions" / f"{ts}_console"
    d.mkdir(parents=True, exist_ok=True)
    _cache_path = d / "console.jsonl"
    return _cache_path


def log_event(event: dict) -> None:
    """Append one JSON object per line for `event`. Never raises.

    Adds `ts` (unix float) and `seq` (0-based per-process counter) unless the
    event already carries those keys (caller data wins). An `image_b64` key is
    replaced by "<image>" in the written line; the caller's dict is untouched.
    """
    global _seq
    if os.getenv("PALIV_SESSION_LOG") == "0":
        return
    try:
        rec = dict(event)
        rec.setdefault("ts", time.time())
        rec.setdefault("seq", _seq)
        _seq += 1
        if "image_b64" in rec:
            rec["image_b64"] = "<image>"
        path = session_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass