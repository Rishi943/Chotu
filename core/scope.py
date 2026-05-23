"""Scope state machine for habit workflows (explore today, investigate later).

Pure data + pure functions. No async, no Pi calls, no LLM calls. Async wrappers
that call these mutators live in core/explore_tools.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    """Update current_x by delta, wrapping mod 12. Returns the new x."""
    state.current_x = (state.current_x + delta) % 12
    return state.current_x


def record_photo_state(
    state: ExploreState,
    anchors: list[str],
    objects: list[str],
    description: str,
    open_path: bool = False,
    forward_steps: int | None = None,
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
    }
    state.current_node_photos.append(photo)
    if open_path:
        state.current_node_open_path = {"x": state.current_x, "forward_steps": forward_steps}
    return None
