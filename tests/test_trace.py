import json
import os
from pathlib import Path
from core import trace


def test_session_dir_is_stable_within_a_session(tmp_path, monkeypatch):
    monkeypatch.setenv("PALIV_OUT", str(tmp_path))
    monkeypatch.delenv("PALIV_TRACE_DIR", raising=False)
    d1 = trace.session_dir("fable")
    os.environ["PALIV_TRACE_DIR"] = str(d1)
    d2 = trace.session_dir("fable")
    assert d1 == d2
    assert d1.parent.name == "sessions"


def test_record_appends_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("PALIV_OUT", str(tmp_path))
    d = trace.session_dir("fable")
    monkeypatch.setenv("PALIV_TRACE_DIR", str(d))
    trace.record("action", "pose", {"name": "push up"}, {"status": "started"})
    trace.record("observation", "get_battery", {}, {"percent": 80})
    lines = (d / "trace.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["action"]["tool"] == "pose"
    assert rec["seq"] == 0


def test_save_frame_writes_and_returns_relpath(tmp_path, monkeypatch):
    monkeypatch.setenv("PALIV_OUT", str(tmp_path))
    d = trace.session_dir("fable")
    monkeypatch.setenv("PALIV_TRACE_DIR", str(d))
    rel = trace.save_frame(b"\xff\xd8\xff\xd9")
    assert rel.startswith("frames/")
    assert (d / rel).read_bytes() == b"\xff\xd8\xff\xd9"
