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
