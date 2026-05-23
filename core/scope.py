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
