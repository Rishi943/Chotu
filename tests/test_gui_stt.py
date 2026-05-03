"""Tests for /stt endpoint in gui_server."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    import chotu.brain as brain
    brain.continuous_mode = False
    from chotu.gui_server import app
    return TestClient(app)


def test_stt_endpoint_enables_continuous_mode(client, monkeypatch):
    import chotu.brain as brain
    resp = client.post("/stt", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert brain.continuous_mode is True


def test_stt_endpoint_disables_continuous_mode(client, monkeypatch):
    import chotu.brain as brain
    brain.continuous_mode = True
    resp = client.post("/stt", json={"enabled": False})
    assert resp.status_code == 200
    assert brain.continuous_mode is False


def test_stt_endpoint_defaults_to_false_if_missing_key(client, monkeypatch):
    import chotu.brain as brain
    brain.continuous_mode = True
    resp = client.post("/stt", json={})
    assert resp.status_code == 200
    assert brain.continuous_mode is False
