from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

import core.brain as brain
from core.gui_server import app

client = TestClient(app)


def test_config_reports_flag(monkeypatch):
    monkeypatch.setattr(brain, "PTT_ENABLED", True)
    assert client.get("/api/config").json() == {"ptt_enabled": True}
    monkeypatch.setattr(brain, "PTT_ENABLED", False)
    assert client.get("/api/config").json() == {"ptt_enabled": False}


def test_ptt_endpoint_triggers_capture_when_enabled(monkeypatch):
    monkeypatch.setattr(brain, "PTT_ENABLED", True)
    mock = AsyncMock()
    monkeypatch.setattr(brain, "trigger_ptt_capture", mock)
    resp = client.post("/ptt")
    assert resp.json() == {"ok": True}
    assert mock.called


def test_ptt_endpoint_disabled(monkeypatch):
    monkeypatch.setattr(brain, "PTT_ENABLED", False)
    mock = AsyncMock()
    monkeypatch.setattr(brain, "trigger_ptt_capture", mock)
    resp = client.post("/ptt")
    assert resp.json()["ok"] is False
    assert not mock.called


def test_handsfree_endpoint_sets_mode(monkeypatch):
    monkeypatch.setattr(brain, "PTT_ENABLED", True)
    mock = MagicMock()
    monkeypatch.setattr(brain, "set_handsfree", mock)
    resp = client.post("/handsfree", json={"enabled": True})
    assert resp.json() == {"ok": True}
    mock.assert_called_once_with(True)


def test_handsfree_endpoint_disabled(monkeypatch):
    monkeypatch.setattr(brain, "PTT_ENABLED", False)
    mock = MagicMock()
    monkeypatch.setattr(brain, "set_handsfree", mock)
    resp = client.post("/handsfree", json={"enabled": True})
    assert resp.json()["ok"] is False
    assert not mock.called
