"""Unit tests for core.picker — offline, no LLM."""

import json

import pytest

from core.llm_client import (
    LLMResponse, NormalizedChoice, NormalizedMessage, ToolCall, ToolFunction,
)
from core.picker import FALLBACK_PICK, Pick, _validate


def _resp(name: str | None, args: str | None) -> LLMResponse:
    """Build a single-choice LLMResponse with one tool_call (or none if name is None)."""
    if name is None:
        tcs = None
    else:
        tcs = [ToolCall(id="x", function=ToolFunction(name=name, arguments=args or ""))]
    return LLMResponse(choices=[NormalizedChoice(message=NormalizedMessage(content=None, tool_calls=tcs))])


def test_valid_idle_pick():
    r = _resp("pick_habit", json.dumps({"state": "idle", "name": "dangle_paws"}))
    assert _validate(r) == Pick("idle", "dangle_paws")


def test_valid_play_pick():
    r = _resp("pick_habit", json.dumps({"state": "play", "name": "explore"}))
    assert _validate(r) == Pick("play", "explore")


def test_no_tool_calls_falls_back():
    r = _resp(None, None)
    assert _validate(r) == FALLBACK_PICK


def test_wrong_tool_name_falls_back():
    r = _resp("speak", json.dumps({"text": "hi"}))
    assert _validate(r) == FALLBACK_PICK


def test_invalid_json_args_falls_back():
    r = _resp("pick_habit", "not json")
    assert _validate(r) == FALLBACK_PICK


def test_unknown_state_falls_back():
    r = _resp("pick_habit", json.dumps({"state": "listen", "name": "do_nothing"}))
    assert _validate(r) == FALLBACK_PICK


def test_unknown_idle_name_falls_back():
    r = _resp("pick_habit", json.dumps({"state": "idle", "name": "moonwalk"}))
    assert _validate(r) == FALLBACK_PICK


def test_unknown_play_name_falls_back():
    r = _resp("pick_habit", json.dumps({"state": "play", "name": "find_object"}))
    assert _validate(r) == FALLBACK_PICK


def test_missing_required_field_falls_back():
    r = _resp("pick_habit", json.dumps({"state": "idle"}))
    assert _validate(r) == FALLBACK_PICK
