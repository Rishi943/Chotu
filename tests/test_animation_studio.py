"""The studio proxy forwards the right body to the Pi and degrades gracefully."""

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts import animation_studio as studio


class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data


@pytest.fixture
def client():
    return TestClient(studio.app)


def test_set_legs_forwards_legs_and_speed(monkeypatch, client):
    calls = {}

    async def fake_request(method, url, json=None, timeout=None):
        calls.update(method=method, url=url, json=json)
        return _FakeResp({"ok": True, "tool": "set_legs"})

    monkeypatch.setattr(studio._client, "request", fake_request)
    r = client.post("/set_legs", json={"legs": [[60, 0, -30]] * 4, "speed": 55})

    assert r.status_code == 200
    assert calls["method"] == "POST"
    assert calls["url"].endswith("/set_legs")
    assert calls["json"] == {"legs": [[60, 0, -30]] * 4, "speed": 55}


def test_pose_forwards_name_and_speed(monkeypatch, client):
    calls = {}

    async def fake_request(method, url, json=None, timeout=None):
        calls.update(method=method, url=url, json=json)
        return _FakeResp({"ok": True, "tool": "pose"})

    monkeypatch.setattr(studio._client, "request", fake_request)
    r = client.post("/pose", json={"name": "stand", "speed": 40})

    assert r.status_code == 200
    assert calls["url"].endswith("/pose")
    assert calls["json"] == {"name": "stand", "speed": 40}


def test_pi_unreachable_returns_502(monkeypatch, client):
    async def boom(method, url, json=None):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(studio._client, "request", boom)
    r = client.get("/health")

    assert r.status_code == 502
    assert r.json()["ok"] is False
    assert "pi_unreachable" in r.json()["error"]
