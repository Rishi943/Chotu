"""Integration test: brain._process drives a full explore through mocked LLM."""

import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def _make_tool_call(call_id: str, name: str, args: dict):
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _make_response(content: str | None, tool_calls: list | None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.mark.asyncio
async def test_active_scope_global_exists():
    """active_scope global is exposed by brain and initially None."""
    from core import brain
    assert hasattr(brain, "active_scope")
    assert brain.active_scope is None


@pytest.mark.asyncio
async def test_explore_entry_opens_scope_and_returns_workflow_message():
    from core import brain
    from core.habits import explore_entry
    brain.active_scope = None  # reset
    msg = await explore_entry(pi=None, brain_module=brain, tool_call_id="call_xyz", assistant_idx=2)
    assert brain.active_scope is not None
    assert brain.active_scope.originating_tool_call_id == "call_xyz"
    assert brain.active_scope.originating_tool_name == "explore"
    assert msg["role"] == "user"
    assert "Explore" in msg["content"] or "explore" in msg["content"].lower()
    brain.active_scope = None
