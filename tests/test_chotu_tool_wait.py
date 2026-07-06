import json
import subprocess
import sys
import threading
import time


def test_wait_for_event_timeout_returns_quickly(tmp_path):
    # timeout=1, voice off, no text written -> {"event":"timeout"} in ~1s
    out = subprocess.run(
        [sys.executable, "-m", "scripts.robot.chotu_tool", "wait_for_event", '{"timeout": 1}'],
        capture_output=True, text=True,
        env={"PALIV_VOICE": "0", "PALIV_WAIT_INPUT": str(tmp_path / "wi"), "PATH": "/usr/bin:/bin"},
        cwd=".", timeout=10,
    )
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert payload["event"] == "timeout"
    assert payload["waited_s"] >= 1


def test_wait_for_event_returns_on_text(tmp_path):
    p = tmp_path / "wi"

    def writer():
        time.sleep(0.5)
        p.write_text("come here")

    threading.Thread(target=writer, daemon=True).start()
    out = subprocess.run(
        [sys.executable, "-m", "scripts.robot.chotu_tool", "wait_for_event", '{"timeout": 10}'],
        capture_output=True, text=True,
        env={"PALIV_VOICE": "0", "PALIV_WAIT_INPUT": str(p), "PATH": "/usr/bin:/bin"},
        cwd=".", timeout=15,
    )
    payload = json.loads(out.stdout.strip().splitlines()[-1])
    assert payload["event"] == "text"
    assert payload["text"] == "come here"
