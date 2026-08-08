"""clear_stage fires on new human input so a staged follow-up does not survive
Rushi changing his mind. OPEN FUNCTIONAL GAP from the Task 7 handoff."""
from unittest.mock import MagicMock, call, patch

from fastapi.testclient import TestClient

import core.brain as brain
from core.gui_server import app

client = TestClient(app)


def test_chat_clears_staged_move_before_pushing(monkeypatch):
    parent = MagicMock()
    runner = MagicMock()
    pending = MagicMock()
    parent.attach_mock(runner, "motion_runner")
    parent.attach_mock(pending, "pending_input")
    monkeypatch.setattr(brain, "motion_runner", runner)
    monkeypatch.setattr(brain, "pending_input", pending)
    resp = client.post("/chat", json={"text": "stop walking"})
    assert resp.json()["ok"] is True
    runner.clear_stage.assert_called_once()
    pending.push.assert_called_once_with("stop walking")
    # clear_stage must happen BEFORE push, not after
    assert parent.mock_calls == [
        call.motion_runner.clear_stage(),
        call.pending_input.push("stop walking"),
    ]


def test_audio_clears_staged_move_before_pushing(monkeypatch):
    parent = MagicMock()
    runner = MagicMock()
    pending = MagicMock()
    parent.attach_mock(runner, "motion_runner")
    parent.attach_mock(pending, "pending_input")
    monkeypatch.setattr(brain, "motion_runner", runner)
    monkeypatch.setattr(brain, "pending_input", pending)

    async def fake_hear(raw, mime):
        return {"text": "walk forward", "language": "Marathi", "ms": 230}

    with patch("core.gui_server.hear", side_effect=fake_hear):
        resp = client.post("/audio", files={"audio": ("test.wav", b"RIFF\x00\x00\x00\x00WAVE", "audio/wav")})
    assert resp.json()["ok"] is True
    runner.clear_stage.assert_called_once()
    pending.push.assert_called_once_with("walk forward")
    # clear_stage must happen BEFORE push, not after
    assert parent.mock_calls == [
        call.motion_runner.clear_stage(),
        call.pending_input.push("walk forward"),
    ]


def test_stop_already_clears_stage():
    """Sanity: /stop was already wired (commit 862babe). Confirm it still is."""
    import inspect, core.gui_server
    source = inspect.getsource(core.gui_server)
    assert "clear_stage" in source
