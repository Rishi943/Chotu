"""Explore subagent: runs an isolated mapping loop with its own message list."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from core.explore.tools import SCOPE_TOOL_SCHEMAS, build_scope_dispatch
from core.pi_client import PiClient
from core.explore.scope import Scope, ExploreState

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPLORE_PROMPT = (REPO_ROOT / "docs" / "EXPLORE.md").read_text(encoding="utf-8")

MAX_NODES = int(os.getenv("PALIV_EXPLORE_MAX_NODES", "5"))
MAX_TURNS_PER_NODE = int(os.getenv("PALIV_EXPLORE_MAX_TURNS_PER_NODE", "30"))

# Module-level LLM client — monkeypatch-friendly
from core.llm_client import LLMClient
llm_client = LLMClient()


def _strip_internal(messages: list[dict]) -> list[dict]:
    """Remove internal tracking keys (prefixed with '_') before sending to LLM."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


def _evict_old_images(messages: list[dict]) -> None:
    """Replace JPEG bytes in old capture results with a stub. Keeps last 2."""
    capture_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "tool" and "image_b64" in str(m.get("content", ""))
    ]
    if len(capture_indices) <= 2:
        return
    for i in capture_indices[:-2]:
        msg = messages[i]
        try:
            content = json.loads(msg["content"])
            if isinstance(content, dict) and "result" in content:
                content["result"].pop("image_b64", None)
                content["result"]["image_evicted"] = True
                msg["content"] = json.dumps(content)
        except (json.JSONDecodeError, TypeError, KeyError):
            pass


async def _dispatch(dispatch_map: dict, name: str, args: dict) -> dict:
    """Call a tool by name from the dispatch map, passing args as JSON string."""
    from core.tools import dispatch_tool
    return await dispatch_tool(dispatch_map, name, json.dumps(args))


async def run_explore(pi: PiClient, *, reason: str = "idle") -> dict:
    """Run the explore subagent. Returns summary envelope."""
    scope = Scope(
        scope_id=f"explore-sub-{os.getpid()}",
        originating_tool_call_id="",
        originating_tool_name="explore",
        state=ExploreState(),
    )

    messages: list[dict] = [
        {"role": "system", "content": EXPLORE_PROMPT, "_origin": "boot"},
        {"role": "user", "content": f"Begin exploring. Reason: {reason}", "_origin": "boot"},
    ]

    dispatch_map = build_scope_dispatch(pi, scope)
    turns_in_current_node = 0
    status = "error"
    concluded_notes = ""

    try:
        while True:
            response = await llm_client.chat_complete(
                _strip_internal(messages), SCOPE_TOOL_SCHEMAS, thinking=False,
            )
            assistant_msg = llm_client.format_assistant_message(response)
            messages.append({**assistant_msg, "_origin": "subagent"})

            tool_calls = assistant_msg.get("tool_calls") or []
            if not tool_calls:
                status = "done"
                concluded_notes = assistant_msg.get("content") or ""
                break

            done = False
            for tc in tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments or "{}")

                if name == "conclude":
                    concluded_notes = args.get("notes", "")
                    status = "done"
                    if scope.state.current_node_id > 0:
                        await _dispatch(dispatch_map, "return_to_origin", {})
                    done = True
                    break

                result = await _dispatch(dispatch_map, name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                    "_origin": "subagent",
                })

                if (name == "commit_node_and_advance"
                        and result.get("ok")
                        and result.get("result", {}).get("advanced")):
                    turns_in_current_node = 0
                    current_node_count = scope.state.current_node_id
                    if current_node_count >= MAX_NODES:
                        await _dispatch(dispatch_map, "return_to_origin", {})
                        status = "cap_nodes"
                        done = True
                        break
                else:
                    turns_in_current_node += 1

                if turns_in_current_node >= MAX_TURNS_PER_NODE:
                    res = await _dispatch(dispatch_map, "commit_node_and_advance", {})
                    if not res.get("ok"):
                        status = "node_fuse"
                        done = True
                        break
                    turns_in_current_node = 0

            if done:
                break
            _evict_old_images(messages)

    except Exception as e:
        log.exception("explore subagent error")
        return {"status": "error", "nodes_added": [], "anchors_seen": [], "message": str(e)}

    nodes_added = [n.get("id", f"node-{i+1:03d}") for i, n in enumerate(scope.state.nodes)]
    anchors = sorted({a for n in scope.state.nodes for a in n.get("anchors_summary", [])})
    msg = concluded_notes or f"Mapped {len(nodes_added)} nodes; cap={status}"

    return {
        "status": status,
        "nodes_added": nodes_added,
        "anchors_seen": anchors,
        "message": msg,
    }
