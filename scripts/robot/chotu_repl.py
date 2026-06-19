"""
Chotu interactive REPL for Claude-as-the-model testing.

Claude drives the explore workflow turn-by-turn:
  python -m scripts.robot.chotu_repl <tool> [json_args]
  python -m scripts.robot.chotu_repl --reset        # wipe state and start fresh
  python -m scripts.robot.chotu_repl --state        # print current state

Available tools in explore scope:
  move            {"direction": "turn left"|"turn right"}
  capture_vision  {}
  record_photo    {"anchors": [...], "objects": [...], "description": "...",
                   "open_path": false, "forward_steps": N, "distance_estimate_cm": N}
  commit_node_and_advance  {}
  return_to_origin         {}
  conclude        {"status": "done"|"inconclusive", "notes": "..."}

  get_distance    {}
  get_battery     {}
  speak           {"text": "..."}

capture_vision also saves the JPEG to /tmp/chotu_capture.jpg for Claude to read.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

STATE_PATH = Path("/tmp/chotu_explore.json")
CAPTURE_PATH = Path("/tmp/chotu_capture.jpg")


def _load_state():
    from core.explore.scope import Scope, ExploreState

    if STATE_PATH.exists():
        raw = json.loads(STATE_PATH.read_text())
        s = raw["state"]
        state = ExploreState(
            current_node_id=s["current_node_id"],
            current_x=s["current_x"],
            nodes=s["nodes"],
            current_node_photos=s["current_node_photos"],
            current_node_open_paths={int(k): v for k, v in s["current_node_open_paths"].items()},
            path_stack=s["path_stack"],
            failed_advances=s["failed_advances"],
            returned_to_origin=s["returned_to_origin"],
        )
        scope = Scope(
            scope_id=raw["scope_id"],
            originating_tool_call_id="repl",
            originating_tool_name="explore",
            state=state,
        )
        return scope
    return _fresh_scope()


def _fresh_scope():
    from core.explore.scope import Scope, ExploreState
    import uuid

    return Scope(
        scope_id=f"repl-{uuid.uuid4().hex[:8]}",
        originating_tool_call_id="repl",
        originating_tool_name="explore",
        state=ExploreState(),
    )


def _save_state(scope):
    s = scope.state
    data = {
        "scope_id": scope.scope_id,
        "state": {
            "current_node_id": s.current_node_id,
            "current_x": s.current_x,
            "nodes": s.nodes,
            "current_node_photos": s.current_node_photos,
            "current_node_open_paths": {str(k): v for k, v in s.current_node_open_paths.items()},
            "path_stack": s.path_stack,
            "failed_advances": s.failed_advances,
            "returned_to_origin": s.returned_to_origin,
        },
    }
    STATE_PATH.write_text(json.dumps(data, indent=2))


async def _run(tool: str, args: dict) -> dict:
    from core.pi_client import PiClient
    from core.explore.tools import (
        scoped_move, scoped_capture_vision, scoped_record_photo,
        scoped_commit_node_and_advance, scoped_return_to_origin, scoped_conclude,
    )
    from core.tools import _do_speak

    pi_host = os.getenv("PI_HOST", "http://chotu.local:7000")
    pi = PiClient(pi_host)
    scope = _load_state()

    try:
        if tool == "move":
            result = await scoped_move(pi, scope, **args)
        elif tool == "capture_vision":
            result = await scoped_capture_vision(pi, scope)
            # Save JPEG so Claude can view it
            b64 = (result.get("result") or {}).get("image_base64", "") or (result.get("result") or {}).get("image_b64", "")
            if b64:
                CAPTURE_PATH.write_bytes(base64.b64decode(b64))
                result["_saved_to"] = str(CAPTURE_PATH)
                # Strip b64 from printed result to keep output readable
                result["result"] = {k: v for k, v in result["result"].items() if k != "image_b64"}
                result["result"]["image_saved"] = str(CAPTURE_PATH)
        elif tool == "record_photo":
            result = await scoped_record_photo(scope, **args)
        elif tool == "commit_node_and_advance":
            result = await scoped_commit_node_and_advance(pi, scope)
        elif tool == "return_to_origin":
            result = await scoped_return_to_origin(pi, scope)
        elif tool == "conclude":
            result = await scoped_conclude(scope, **args)
        elif tool == "get_distance":
            result = await pi.get_distance()
        elif tool == "get_battery":
            result = await pi.get_battery()
        elif tool == "speak":
            result = await _do_speak(face_pi=pi, muted=False, **args)
        else:
            result = {"ok": False, "error": f"unknown tool: {tool}"}

        _save_state(scope)
        return result

    finally:
        await pi.close()


def _print_state():
    if not STATE_PATH.exists():
        print("No state. Run --reset or any tool to initialise.")
        return
    data = json.loads(STATE_PATH.read_text())
    s = data["state"]
    print(f"scope_id       : {data['scope_id']}")
    print(f"current_node   : {s['current_node_id']}")
    print(f"current_x      : {s['current_x']}")
    print(f"nodes_committed: {len(s['nodes'])}")
    print(f"photos_this_node: {len(s['current_node_photos'])}")
    print(f"open_paths     : {list(s['current_node_open_paths'].keys())}")
    print(f"failed_advances: {s['failed_advances']}")
    print(f"returned        : {s['returned_to_origin']}")
    if s["nodes"]:
        print("\nCommitted nodes:")
        for n in s["nodes"]:
            print(f"  node {n['id']}: anchors={n['anchors_summary']}, photos={len(n['photos'])}")


def main():
    args = sys.argv[1:]

    if not args or args[0] == "--help":
        print(__doc__)
        return

    if args[0] == "--reset":
        STATE_PATH.unlink(missing_ok=True)
        CAPTURE_PATH.unlink(missing_ok=True)
        print("State reset.")
        return

    if args[0] == "--state":
        _print_state()
        return

    tool = args[0]
    tool_args = json.loads(args[1]) if len(args) > 1 else {}

    result = asyncio.run(_run(tool, tool_args))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
