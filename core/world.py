"""Shared persistent world model — node graph of explored space.

Single writer (the explore subagent), readable by the parent Chotu loop.
JSON-backed at data/world.json; saved on every mutation.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
WORLD_PATH = REPO_ROOT / "data" / "world.json"

_GRAPH: dict = {"nodes": {}, "origin_node": None, "version": 1}


def load() -> None:
    """Load world from disk into _GRAPH. Tolerates missing/corrupt files."""
    global _GRAPH
    if not WORLD_PATH.exists():
        _GRAPH = {"nodes": {}, "origin_node": None, "version": 1}
        return
    try:
        _GRAPH = json.loads(WORLD_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("world.json corrupt or unreadable (%s); starting empty", e)
        _GRAPH = {"nodes": {}, "origin_node": None, "version": 1}


def save() -> None:
    """Persist _GRAPH to disk. Best-effort — logs on failure, doesn't raise."""
    try:
        WORLD_PATH.parent.mkdir(parents=True, exist_ok=True)
        WORLD_PATH.write_text(json.dumps(_GRAPH, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("world.save failed: %s", e)


def _next_node_id() -> str:
    n = len(_GRAPH["nodes"]) + 1
    return f"node-{n:03d}"


def add_node(x: int, y: int, heading: int) -> str:
    nid = _next_node_id()
    _GRAPH["nodes"][nid] = {
        "id": nid,
        "x": x, "y": y,
        "heading_at_scan_start": heading,
        "anchors": [],
        "objects": [],
        "photos": [],
        "exits": [],
        "created_at": int(time.time()),
    }
    if _GRAPH["origin_node"] is None:
        _GRAPH["origin_node"] = nid
    save()
    return nid


def add_photo(node_id: str, *, photo_idx: int, heading: int, description: str,
              anchors_in_photo: list[str], objects_in_photo: list[str],
              open_path: bool, forward_steps: int | None = None,
              distance_cm: int | None = None) -> None:
    node = _GRAPH["nodes"][node_id]
    # Same-heading replace
    node["photos"] = [p for p in node["photos"] if p["heading"] != heading]
    node["photos"].append({
        "photo_idx": photo_idx,
        "heading": heading,
        "description": description,
        "anchors_in_photo": anchors_in_photo,
        "objects_in_photo": objects_in_photo,
        "open_path": open_path,
        "forward_steps": forward_steps,
        "distance_cm": distance_cm,
    })
    # Roll up anchors/objects into node-level sets (preserve insertion order)
    for a in anchors_in_photo:
        if a not in node["anchors"]:
            node["anchors"].append(a)
    for o in objects_in_photo:
        if o not in node["objects"]:
            node["objects"].append(o)
    save()


def add_exit(from_node: str, *, heading: int, to_node: str, forward_steps: int) -> None:
    node = _GRAPH["nodes"][from_node]
    for ex in node["exits"]:
        if ex["heading"] == heading and ex["to_node"] == to_node:
            return  # dedup
    node["exits"].append({"heading": heading, "to_node": to_node, "forward_steps": forward_steps})
    save()


def get_node(node_id: str) -> dict:
    return _GRAPH["nodes"][node_id]


def list_nodes() -> list[dict]:
    return list(_GRAPH["nodes"].values())


def origin() -> str | None:
    return _GRAPH["origin_node"]
