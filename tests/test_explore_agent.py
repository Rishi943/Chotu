"""Subagent integration test with faked LLM responses and faked Pi."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.explore import agent as explore_agent
from core import world


@pytest.fixture
def fake_pi():
    pi = MagicMock()
    pi.move = AsyncMock(return_value={"ok": True, "tool": "move", "result": {}, "duration_ms": 10, "timestamp": 0, "error": None})
    pi.capture = AsyncMock(return_value={"ok": True, "tool": "capture", "result": {"image_b64": "AAAA"}, "duration_ms": 10, "timestamp": 0, "error": None})
    return pi


@pytest.fixture(autouse=True)
def isolated_world(tmp_path, monkeypatch):
    p = tmp_path / "world.json"
    monkeypatch.setattr(world, "WORLD_PATH", p)
    world._GRAPH = {"nodes": {}, "origin_node": None, "version": 1}


@pytest.fixture
def fake_llm(monkeypatch):
    """Scripted LLM that always calls `conclude` immediately."""
    class _FakeClient:
        def __init__(self):
            self._calls = 0

        async def chat_complete(self, messages, tools, thinking=False, **kwargs):
            return self._next_response()

        def format_assistant_message(self, response):
            return {"role": "assistant", "content": response.get("content"),
                    "tool_calls": response.get("tool_calls", [])}

        def _next_response(self):
            self._calls += 1
            if self._calls == 1:
                return {"content": "ok", "tool_calls": [
                    _tc("conclude", {"notes": "test stub"})
                ]}
            return {"content": "done", "tool_calls": []}

    def _tc(name, args):
        import json
        m = MagicMock()
        m.id = f"call-{name}"
        m.function = MagicMock()
        m.function.name = name
        m.function.arguments = json.dumps(args)
        return m

    fake = _FakeClient()
    monkeypatch.setattr(explore_agent, "llm_client", fake)
    return fake


@pytest.mark.asyncio
async def test_run_explore_concludes_cleanly(fake_pi, fake_llm):
    envelope = await explore_agent.run_explore(fake_pi, reason="test")
    assert envelope["status"] == "done"
    assert "nodes_added" in envelope
    assert "message" in envelope


@pytest.mark.asyncio
async def test_run_explore_respects_max_nodes(fake_pi, monkeypatch):
    """If subagent keeps advancing without concluding, MAX_NODES caps it."""
    class _Spammer:
        def __init__(self): self._n = 0
        async def chat_complete(self, messages, tools, thinking=False, **kw):
            self._n += 1
            if self._n > 50:
                return {"content": "", "tool_calls": []}
            tc = MagicMock()
            tc.id = f"c-{self._n}"
            tc.function = MagicMock()
            tc.function.name = "commit_node_and_advance"
            tc.function.arguments = "{}"
            return {"content": None, "tool_calls": [tc]}
        def format_assistant_message(self, r):
            return {"role": "assistant", "content": r.get("content"),
                    "tool_calls": r.get("tool_calls", [])}

    monkeypatch.setattr(explore_agent, "llm_client", _Spammer())
    monkeypatch.setattr(explore_agent, "MAX_NODES", 2)
    envelope = await explore_agent.run_explore(fake_pi, reason="test")
    assert envelope["status"] in ("cap_nodes", "node_fuse", "error", "done")
