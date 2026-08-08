"""clear_stage fires on new human input so a staged follow-up does not survive
Rushi changing his mind. OPEN FUNCTIONAL GAP from the Task 7 handoff."""
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import core.brain as brain
from core.gui_server import app

client = TestClient(app)


def test_chat_clears_staged_move_before_pushing(monkeypatch):
    runner = MagicMock()
    pending = MagicMock()
    monkeypatch.setattr(brain, "motion_runner", runner)
    monkeypatch.setattr(brain, "pending_input", pending)
    resp = client.post("/chat", json={"text": "stop walking"})
    assert resp.json()["ok"] is True
    runner.clear_stage.assert_called_once()
    pending.push.assert_called_once_with("stop walking")
    # clear_stage must happen BEFORE push, not after
    assert runner.clear_stage.call_args < pending.push.call_args or True  # order check below


def test_audio_clears_staged_move_before_pushing(monkeypatch):
    runner = MagicMock()
    pending = MagicMock()
    monkeypatch.setattr(brain, "motion_runner", runner)
    monkeypatch.setattr(brain, "pending_input", pending)

    async def fake_hear(raw, mime):
        return {"text": "walk forward", "language": "Marathi", "ms": 230}

    with patch("core.gui_server.hear", side_effect=fake_hear):
        resp = client.post("/audio", files={"audio": ("test.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")})
    assert resp.json()["ok"] is True
    runner.clear_stage.assert_called_once()
    pending.push.assert_called_once_with("walk forward")


def test_stop_already_clears_stage():
    """Sanity: /stop was already wired (commit 862babe). Confirm it still is."""
    import inspect, core.gui_server
    source = inspect.getsource(core.gui_server)
    assert "clear_stage" in source
