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
    r = _resp("pick_habit", json.dumps({"state": "idle", "name": "yawn"}))
    assert _validate(r) == Pick("idle", "yawn")


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


class _FakeLLM:
    """Stand-in for LLMClient with a scripted response or exception."""
    def __init__(self, response=None, raises: Exception | None = None):
        self._response = response
        self._raises = raises
        self.calls: list[dict] = []

    async def chat_complete(self, messages, tools, thinking=False, tool_choice=None, max_tokens=None):
        self.calls.append({
            "messages": messages, "tools": tools, "thinking": thinking,
            "tool_choice": tool_choice, "max_tokens": max_tokens,
        })
        if self._raises:
            raise self._raises
        return self._response


@pytest.mark.asyncio
async def test_pick_next_returns_validated_pick():
    from core.picker import PickerInput, pick_next
    r = _resp("pick_habit", json.dumps({"state": "idle", "name": "yawn"}))
    llm = _FakeLLM(response=r)
    pick = await pick_next(PickerInput(current_state="idle", recent_picks=["do_nothing"]), llm)
    assert pick == Pick("idle", "yawn")
    assert llm.calls[0]["thinking"] is True
    assert llm.calls[0]["max_tokens"] == 1024
    assert llm.calls[0]["tool_choice"]["function"]["name"] == "pick_habit"


@pytest.mark.asyncio
async def test_pick_next_falls_back_on_llm_exception():
    from core.picker import PickerInput, pick_next
    llm = _FakeLLM(raises=RuntimeError("llama-server down"))
    pick = await pick_next(PickerInput(current_state="idle", recent_picks=[]), llm)
    assert pick == FALLBACK_PICK


@pytest.mark.asyncio
async def test_pick_next_renders_empty_history_as_none_yet():
    from core.picker import PickerInput, pick_next
    r = _resp("pick_habit", json.dumps({"state": "idle", "name": "do_nothing"}))
    llm = _FakeLLM(response=r)
    await pick_next(PickerInput(current_state="idle", recent_picks=[]), llm)
    user_msg = llm.calls[0]["messages"][-1]
    assert user_msg["role"] == "user"
    assert "none yet" in user_msg["content"]
