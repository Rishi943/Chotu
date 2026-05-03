"""Tests for chotu/spells.py — mocks pi.set_legs and httpx."""

import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("HA_BASE_URL", "http://localhost:8123")
os.environ.setdefault("HA_TOKEN", "test-token")
os.environ.setdefault("HA_LIGHT_ENTITY", "light.test")


@pytest.fixture
def pi():
    m = MagicMock()
    m.set_legs = AsyncMock(return_value={"ok": True, "tool": "set_legs", "result": {}, "duration_ms": 10, "timestamp": 0, "error": None})
    return m


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class FakeClient:
    def __init__(self, status_code=200):
        self._status_code = status_code
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json})
        return FakeResponse(self._status_code)


def test_lumos_calls_turn_on(pi):
    from chotu.spells import cast_lumos
    client = FakeClient()
    with patch("httpx.AsyncClient", return_value=client):
        result = run(cast_lumos(pi))
    assert result["ok"] is True
    assert result["result"]["spell"] == "lumos"
    assert any("turn_on" in c["url"] for c in client.calls)
    assert any(c["json"].get("entity_id") == "light.test" for c in client.calls)


def test_nox_calls_turn_off(pi):
    from chotu.spells import cast_nox
    client = FakeClient()
    with patch("httpx.AsyncClient", return_value=client):
        result = run(cast_nox(pi))
    assert result["ok"] is True
    assert result["result"]["spell"] == "nox"
    assert any("turn_off" in c["url"] for c in client.calls)


def test_avada_kedavra_green_then_off(pi):
    from chotu.spells import cast_avada_kedavra
    client = FakeClient()
    with patch("httpx.AsyncClient", return_value=client), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        result = run(cast_avada_kedavra(pi))
    assert result["ok"] is True
    assert result["result"]["spell"] == "avada_kedavra"
    urls = [c["url"] for c in client.calls]
    assert "turn_on" in urls[0]
    assert "turn_off" in urls[1]
    green_call = client.calls[0]["json"]
    assert green_call.get("rgb_color") == [0, 255, 0]
    assert green_call.get("brightness") == 255


def test_wand_pose_raises_fr_then_returns_neutral(pi):
    from chotu.spells import cast_lumos
    client = FakeClient()
    with patch("httpx.AsyncClient", return_value=client), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        run(cast_lumos(pi))
    calls = pi.set_legs.call_args_list
    assert len(calls) == 2
    wand_legs = calls[0][0][0]
    assert wand_legs[0] != [60, 0, -30]   # FR is raised
    assert wand_legs[1] == [60, 0, -30]   # FL neutral
    assert wand_legs[2] == [60, 0, -30]   # BR neutral
    assert wand_legs[3] == [60, 0, -30]   # BL neutral
    neutral_legs = calls[1][0][0]
    assert neutral_legs == [[60, 0, -30]] * 4


def test_ha_failure_returns_not_ok(pi):
    from chotu.spells import cast_lumos
    client = FakeClient(status_code=500)
    with patch("httpx.AsyncClient", return_value=client), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        result = run(cast_lumos(pi))
    assert result["ok"] is False
    assert result["error"] == "HA call failed"
