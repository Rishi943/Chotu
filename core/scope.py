"""Scope state machine for habit workflows (explore today, investigate later).

Pure data + pure functions. No async, no Pi calls, no LLM calls. Async wrappers
that call these mutators live in core/explore_tools.py.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

TURNS_PER_REVOLUTION = int(os.getenv("PALIV_EXPLORE_TURNS_PER_REVOLUTION", "10"))


@dataclass
class ExploreState:
    current_node_id: int = 0
    current_x: int = 0
    nodes: list[dict] = field(default_factory=list)
    current_node_photos: list[dict] = field(default_factory=list)
    current_node_open_path: dict | None = None  # {"x": int, "forward_steps": int}
    path_stack: list[dict] = field(default_factory=list)  # [{"from_node": int, "open_path_x": int, "forward_steps": int}, ...]
    failed_advances: int = 0
    returned_to_origin: bool | None = None


@dataclass
class Scope:
    scope_id: str
    originating_tool_call_id: str
    originating_tool_name: str
    state: ExploreState
    tagged_message_indexes: list[int] = field(default_factory=list)


def bump_x(state: ExploreState, delta: int) -> int:
    """Update current_x by delta, wrapping mod TURNS_PER_REVOLUTION. Returns the new x."""
    state.current_x = (state.current_x + delta) % TURNS_PER_REVOLUTION
    return state.current_x


def record_photo_state(
    state: ExploreState,
    anchors: list[str],
    objects: list[str],
    description: str,
    open_path: bool = False,
    forward_steps: int | None = None,
    distance_estimate_cm: int | None = None,
) -> str | None:
    """Append a photo entry at current_x. Returns None on success, error string on failure.

    open_path=True requires a positive forward_steps and that no other photo on
    the current node has already been marked open_path.
    """
    if open_path:
        if forward_steps is None or not isinstance(forward_steps, int) or forward_steps <= 0:
            return "open_path=True requires a positive integer forward_steps"
        if state.current_node_open_path is not None:
            return (
                f"open_path already set on this node at x={state.current_node_open_path['x']}; "
                f"only one open_path per node"
            )

    photo = {
        "x": state.current_x,
        "anchors": list(anchors),
        "objects": list(objects),
        "description": description,
        "open_path": bool(open_path),
        "forward_steps": forward_steps if open_path else None,
        "distance_estimate_cm": distance_estimate_cm,
    }
    state.current_node_photos.append(photo)
    if open_path:
        state.current_node_open_path = {"x": state.current_x, "forward_steps": forward_steps}
    return None


def _ordered_unique(items: list[str]) -> list[str]:
    """Dedup a flat list, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def commit_node_state(state: ExploreState) -> tuple[bool, dict]:
    """Finalize current node into state.nodes. Returns (advanced, node_dict).

    advanced=True iff current_node_open_path is set; in that case state is rolled
    forward (node_id+1, current_x=0, photos/open_path cleared, path_stack pushed).
    advanced=False iff terminal — node committed but state left in place for
    return_to_origin or conclude.
    """
    anchors_flat = [a for p in state.current_node_photos for a in p["anchors"]]
    node = {
        "id": state.current_node_id,
        "anchors_summary": _ordered_unique(anchors_flat),
        "photos": list(state.current_node_photos),
    }
    state.nodes.append(node)

    if state.current_node_open_path is None:
        return False, node

    state.path_stack.append({
        "from_node": state.current_node_id,
        "open_path_x": state.current_node_open_path["x"],
        "forward_steps": state.current_node_open_path["forward_steps"],
    })
    state.current_node_id += 1
    state.current_x = 0
    state.current_node_photos = []
    state.current_node_open_path = None
    return True, node


def plan_return_steps(path_stack: list[dict], current_x: int) -> list[tuple[str, int]]:
    """Plan the move sequence to walk back to node 0.

    path_stack is the list of edges in order [node_0→node_1, node_1→node_2, ...].
    current_x is the robot's current heading in the last (terminal) node's frame.

    Returns a flat list of (direction, steps) Pi moves. direction ∈ {"turn right", "forward"}.
    """
    if not path_stack:
        return []
    steps: list[tuple[str, int]] = []
    # Start arrived_at_x as the terminal edge's outbound_x — this is the canonical
    # representation of the robot's arrival heading in the parent's frame after a 360° scan.
    arrived_at_x = path_stack[-1]["open_path_x"]
    prev_open_path_x = None
    for edge in reversed(path_stack):
        outbound_x = edge["open_path_x"]
        forward_steps = edge["forward_steps"]
        if prev_open_path_x is None:
            reorient = (outbound_x - arrived_at_x) % TURNS_PER_REVOLUTION  # 0 on first iteration by construction
        else:
            reorient = (prev_open_path_x - arrived_at_x) % TURNS_PER_REVOLUTION
        steps.append(("turn right", reorient))
        steps.append(("turn right", TURNS_PER_REVOLUTION // 2))
        steps.append(("forward", forward_steps))
        arrived_at_x = (outbound_x + TURNS_PER_REVOLUTION // 2) % TURNS_PER_REVOLUTION
        prev_open_path_x = outbound_x
    if steps and steps[0] == ("turn right", 0):
        steps.pop(0)
    return steps


def build_map(state: ExploreState, notes: str) -> dict:
    return {
        "nodes": list(state.nodes),
        "returned_to_origin": bool(state.returned_to_origin),
        "node_count": len(state.nodes),
        "notes": notes,
    }


def splice_messages(
    messages: list[dict],
    *,
    tagged_indexes: list[int],
    tool_call_id: str,
    result_json: str,
) -> list[dict]:
    """Return a new list with tagged_indexes removed and a synthetic tool result appended."""
    drop = set(tagged_indexes)
    out = [m for i, m in enumerate(messages) if i not in drop]
    out.append({"role": "tool", "tool_call_id": tool_call_id, "content": result_json})
    return out


def open_scope(*, originating_tool_call_id: str, originating_tool_name: str) -> Scope:
    return Scope(
        scope_id=f"{originating_tool_name}-{uuid.uuid4().hex[:8]}",
        originating_tool_call_id=originating_tool_call_id,
        originating_tool_name=originating_tool_name,
        state=ExploreState(),
    )


def tag_message_index(scope: Scope, index: int) -> None:
    scope.tagged_message_indexes.append(index)
