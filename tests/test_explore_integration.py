"""Integration test: brain dispatches explore as a plain tool calling subagent."""

import pytest

pytest.skip(
    "explore is deferred from the main brain — re-enable when dispatch_explore_tool is rewired",
    allow_module_level=True,
)

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
async def test_explore_tool_dispatches_subagent():
    """The `explore` tool in main scope should await run_explore and return its envelope."""
    from core.brain import dispatch_explore_tool
    from unittest.mock import AsyncMock, patch
    fake_pi = AsyncMock()
    fake_envelope = {"status": "done", "nodes_added": ["node-001"],
                     "anchors_seen": ["wall"], "message": "test"}
    with patch("core.brain.explore_agent") as mod:
        mod.run_explore = AsyncMock(return_value=fake_envelope)
        result = await dispatch_explore_tool(fake_pi, {"reason": "idle"})
    assert result["ok"] is True
    assert result["tool"] == "explore"
    assert result["result"] == fake_envelope
