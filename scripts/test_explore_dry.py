"""End-to-end dry run: explore subagent against a fake Pi, real LLM.

Usage: python -m scripts.test_explore_dry
Requires llama-server running on :8080 with a small model.
"""

import asyncio
import json
import logging
import sys
from unittest.mock import AsyncMock, MagicMock

from core import explore_agent, world


async def main() -> int:
    logging.basicConfig(level=logging.INFO)

    # Reset world to a temp file
    world.WORLD_PATH = world.WORLD_PATH.parent / "world.dryrun.json"
    if world.WORLD_PATH.exists():
        world.WORLD_PATH.unlink()
    world._GRAPH = {"nodes": {}, "origin_node": None, "version": 1}

    fake_pi = MagicMock()
    fake_pi.move = AsyncMock(return_value={"ok": True, "tool": "move", "result": {},
                                           "duration_ms": 5, "timestamp": 0, "error": None})
    # Canned image: 1x1 white JPEG b64
    fake_pi.capture = AsyncMock(return_value={
        "ok": True, "tool": "capture",
        "result": {"image_b64": "/9j/4AAQSkZJRgABAQAAAQABAAD//gA7Q1JFQVRPUjogZ2QtanBlZyB2MS4wIChVc2luZyBJSkcgSlBFRyB2NjIp"},
        "duration_ms": 5, "timestamp": 0, "error": None
    })

    envelope = await explore_agent.run_explore(fake_pi, reason="dryrun")
    print(json.dumps(envelope, indent=2))
    print("---")
    print(json.dumps(world.list_nodes(), indent=2))
    return 0 if envelope["status"] in ("done", "cap_nodes") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
