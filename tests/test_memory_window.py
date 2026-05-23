"""Tests for rolling context window trimming."""

import pytest


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def test_trim_keeps_under_budget():
    from core.brain import trim_memory
    # Each long string ~400 tokens (~1600 chars). 50 of them = ~20k tokens.
    big = "x" * 1600
    items = [_msg("user", big) if i % 2 == 0 else _msg("assistant", big) for i in range(50)]
    trimmed = trim_memory(items, max_tokens=12000)
    # estimate of trimmed should be <= budget (with some slack)
    from core.brain import _estimate_tokens
    assert _estimate_tokens(trimmed) <= 12000
    # newest items preserved
    assert trimmed[-1] == items[-1]


def test_trim_keeps_tool_pairs_together():
    from core.brain import trim_memory
    big = "y" * 1600  # ~400 tokens each
    # Layout: [user-big, assistant-with-tool-calls, tool-result, user-big, assistant-big]
    # With budget 500, trim must drop the front pair as a unit, not just the assistant.
    items = [
        _msg("user", big),
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "result"},
        _msg("user", big),
        _msg("assistant", big),
    ]
    trimmed = trim_memory(items, max_tokens=500)
    # Invariant: every tool message in `trimmed` has its matching assistant tool_call also in `trimmed`.
    call_ids: set[str] = set()
    for m in trimmed:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                call_ids.add(tc["id"])
    for m in trimmed:
        if m.get("role") == "tool":
            assert m["tool_call_id"] in call_ids, \
                f"orphan tool result {m['tool_call_id']} — pair was split"
    # And: under budget after trim.
    from core.brain import _estimate_tokens
    assert _estimate_tokens(trimmed) <= 500
