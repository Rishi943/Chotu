"""Scripted habit-tools — multi-step Pi sequences that look like single tool calls to the LLM.

Each habit is an async function `habit(pi: PiClient) -> dict`, returning a standard envelope.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from core.pi_client import PiClient

logger = logging.getLogger(__name__)


def _envelope(tool: str, result: dict, started_at: float, ok: bool = True, error: str | None = None) -> dict:
    return {
        "ok": ok, "tool": tool, "result": result,
        "duration_ms": int((time.time() - started_at) * 1000),
        "timestamp": time.time(), "error": error,
    }


OBSTACLE_THRESHOLD_CM = 15.0


async def _capture(pi: PiClient) -> dict:
    """Indirection to allow tests to stub capture_vision_tool."""
    from core.tools import capture_vision_tool
    return await capture_vision_tool(pi)


async def investigate(pi: PiClient) -> dict:
    """Look at what's in front of you: distance check, then either look up (if close) or step forward, then capture vision.

    Returns a consolidated envelope summarising the steps + final vision result.
    """
    started = time.time()
    steps: list[dict] = []

    dist_env = await pi.get_distance()
    steps.append({"step": "get_distance", "env": dist_env})
    cm = (dist_env.get("result") or {}).get("cm", 9999)

    if 0 < cm < OBSTACLE_THRESHOLD_CM:
        pose_env = await pi.pose(name="look up", speed=50)
        steps.append({"step": "pose:look_up", "env": pose_env})
    else:
        move_env = await pi.move(direction="forward", steps=2, speed=70)
        steps.append({"step": "move:forward:2", "env": move_env})

    cap_env = await _capture(pi)
    steps.append({"step": "capture_vision", "env": cap_env})

    image_b64 = (cap_env.get("result") or {}).get("image_base64", "")
    summary = {
        "distance_cm": cm,
        "action": "look_up" if 0 < cm < OBSTACLE_THRESHOLD_CM else "step_forward_2",
        "image_base64": image_b64,
        "steps_count": len(steps),
    }
    return _envelope("investigate", summary, started, ok=cap_env.get("ok", False),
                     error=None if cap_env.get("ok") else "investigate: capture_vision failed")


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / "workflows"


async def explore_entry(pi, *, brain_module=None, tool_call_id: str, assistant_idx: int) -> dict:
    """Open an explore scope and return the workflow-doc user message for _process to append.

    Mutates brain_module.active_scope. Caller (brain._process) is responsible for
    appending the returned message to the local `messages` list and tagging its
    index via tag_message_index().
    """
    from core.explore.scope import open_scope
    if brain_module is None:
        from core import brain as brain_module_default
        brain_module = brain_module_default
    workflow_path = WORKFLOWS_DIR / "explore.md"
    workflow_doc = workflow_path.read_text(encoding="utf-8")
    brain_module.active_scope = open_scope(
        originating_tool_call_id=tool_call_id,
        originating_tool_name="explore",
    )
    return {"role": "user", "content": workflow_doc}
