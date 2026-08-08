"""marker: act/scene labels recorded straight into the session trace."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run_marker(tmp_path, payload):
    return subprocess.run(
        [sys.executable, "-m", "scripts.robot.chotu_tool", "marker", payload],
        capture_output=True, text=True, cwd=REPO,
        env={"PALIV_TRACE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(REPO)},
    )


def test_marker_records_observation(tmp_path):
    proc = _run_marker(tmp_path, '{"text": "ACT 1 BEGIN"}')
    assert proc.returncode == 0, proc.stderr
    assert "marker recorded" in proc.stdout
    rec = json.loads((tmp_path / "trace.jsonl").read_text().splitlines()[0])
    assert rec["observation"]["tool"] == "marker"
    assert rec["observation"]["args"]["text"] == "ACT 1 BEGIN"
    assert rec["action"] is None and rec["thought"] is None


def test_marker_empty_text_errors(tmp_path):
    proc = _run_marker(tmp_path, '{"text": ""}')
    assert proc.returncode != 0
    assert not (tmp_path / "trace.jsonl").exists()
