"""peek_over is a reel-persona-gated tool: present only when PALIV_PERSONA=reel."""

import asyncio
from unittest.mock import MagicMock

from core.tools import peek_over_enabled, _PEEK_OVER_SCHEMA, build_dispatch


def test_enabled_only_for_reel():
    assert peek_over_enabled({"PALIV_PERSONA": "reel"}) is True
    assert peek_over_enabled({"PALIV_PERSONA": "base"}) is False
    assert peek_over_enabled({}) is False


def test_schema_shape():
    fn = _PEEK_OVER_SCHEMA["function"]
    assert fn["name"] == "peek_over"
    assert "lead" in fn["parameters"]["properties"]
    assert fn["parameters"]["properties"]["lead"]["enum"] == ["left", "right"]
    assert fn["parameters"]["required"] == ["lead"]


def test_dispatch_includes_peek_over_when_reel(monkeypatch):
    monkeypatch.setenv("PALIV_PERSONA", "reel")
    d = build_dispatch(MagicMock(), asyncio.Event())
    assert "peek_over" in d


def test_dispatch_excludes_peek_over_by_default(monkeypatch):
    monkeypatch.delenv("PALIV_PERSONA", raising=False)
    d = build_dispatch(MagicMock(), asyncio.Event())
    assert "peek_over" not in d
