"""Simulate the live paced loop against the REAL local llama-server with a faked Pi.

Drives core.brain.run_iteration() directly so the new machinery is exercised:
append-only memory + maybe_compact, the Scratchpad [STATE] block, cap_result, and
the frame stack. The Pi is faked: capture() returns rotating real JPEGs from assets/,
and every tool returns a fake envelope (get_distance is unreliable on purpose, so the
state block should mark distance DEAD after 3 reads).

Local llama-server only (free, no cloud). Usage:
    python -m scripts.robot.sim_loop            # 12 iterations
    python -m scripts.robot.sim_loop 20         # N iterations
"""

import asyncio
import base64
import os
import sys
import time
from pathlib import Path

NO_FRAMES = os.getenv("SIM_NO_FRAMES", "0") == "1"

from dotenv import load_dotenv

load_dotenv()

# Optional cloud run: point the brain at DashScope (OpenAI-compatible, "local" path).
# Spends real tokens — only used when SIM_CLOUD=1 is explicitly set.
if os.getenv("SIM_CLOUD") == "1":
    os.environ["PALIV_LLM_PROVIDER"] = "local"
    os.environ["PALIV_BRAIN_URL"] = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    os.environ["PALIV_BRAIN_KEY"] = os.environ["DASHSCOPE_API_KEY"]
    os.environ["PALIV_BRAIN_MODEL"] = os.getenv("SIM_CLOUD_MODEL", "qwen3.5-flash")

import core.brain as brain
from core.loop_helpers import estimate_memory_tokens
from core.scratchpad import Scratchpad

REPO = Path(__file__).resolve().parents[2]
_FRAME_DIR = os.getenv("SIM_FRAME_DIR")
if _FRAME_DIR:
    ASSETS = sorted(Path(_FRAME_DIR).glob("*.jpg"))[:3]
else:
    ASSETS = sorted((REPO / "assets").glob("*.jpeg"))[:3]


def _load_frames() -> list[str]:
    frames = []
    for p in ASSETS:
        frames.append(base64.b64encode(p.read_bytes()).decode("ascii"))
    if not frames:
        raise SystemExit("no JPEGs in assets/ to use as simulated frames")
    return frames


_FRAMES = _load_frames()


def _fake_envelope(tool: str, **args) -> dict:
    base = {"ok": True, "tool": tool, "duration_ms": 5, "timestamp": time.time(), "error": None}
    if tool == "get_distance":
        # Unreliable on purpose — mimics the real ultrasonic bug from the logs.
        return {**base, "result": {"cm": -1.0, "reliable": False}}
    if tool == "move":
        return {**base, "result": {"direction": args.get("direction", "forward"),
                                    "steps_completed": args.get("steps", 1), "halted_early": False}}
    if tool == "pose":
        return {**base, "result": {"pose": args.get("name", "stand"), "held_ms": 500}}
    if tool == "speak":
        return {**base, "result": {"text": args.get("text", ""), "played": True}}
    return {**base, "result": {}}


class FakePi:
    def __init__(self):
        self.i = 0

    async def capture(self) -> dict:
        if NO_FRAMES:
            return {"ok": False, "tool": "capture", "result": {}}
        # Rotate through real frames so the "view" changes each turn.
        b64 = _FRAMES[self.i % len(_FRAMES)]
        self.i += 1
        return {"ok": True, "tool": "capture", "result": {"image_base64": b64}}


def _fake_dispatch_map() -> dict:
    names = ["move", "pose", "speak", "get_distance", "get_battery", "set_face", "wait"]

    def make(n):
        async def fn(**kw):
            return _fake_envelope(n, **kw)
        return fn

    return {n: make(n) for n in names}


def _has_state_block(messages: list[dict]) -> bool:
    return any(isinstance(m.get("content"), str) and m["content"].startswith("[STATE]")
               for m in messages)


async def main():
    iters = int(sys.argv[1]) if len(sys.argv) > 1 else 12

    if os.getenv("SIM_NO_CACHE_MARKER") == "1":
        # Test the hypothesis that the injected cache_control:ephemeral marker caps
        # DashScope's implicit cache at the system message. Disabling it lets the
        # automatic cache match the full text prefix. Non-destructive (runtime only).
        brain.llm_client._cache_system = False
        print("[sim] cache_control marker DISABLED for this run")

    brain.pi = FakePi()
    brain.dispatch_map = _fake_dispatch_map()
    brain._pi_reachable = False
    brain.memory.clear()
    brain.frame_stack.clear()
    brain.scratchpad = Scratchpad()
    brain.pending_input.push("walk around and look for something interesting")

    print(f"\nSim: {iters} iterations against {brain.llm_client.model} | "
          f"COMPACT_AT_TOKENS={brain.COMPACT_AT_TOKENS} KEEP_TOKENS={brain.COMPACT_KEEP_TOKENS} FLOOR={brain.LOOP_FLOOR}s")
    print(f"frames: {[p.name for p in ASSETS]}\n")

    for n in range(1, iters + 1):
        # Snapshot the exact messages this turn sends, to confirm STATE placement.
        msgs = brain.build_loop_messages(brain.SYSTEM_PROMPT, brain.memory,
                                         brain.frame_stack, brain.scratchpad)
        await brain.run_iteration()
        a_turns = sum(1 for m in brain.memory if m.get("role") == "assistant")
        print(f"    └ iter {n:>2}: mem_turns={a_turns:<2} state_block={_has_state_block(msgs)} "
              f"recent={list(brain.scratchpad.recent)} dist_alive={brain.scratchpad.distance_alive}")

    print("\n--- final scratchpad render ---")
    st = brain.scratchpad.render()
    print(st["content"] if st else "(empty)")

    u = brain._usage
    print("\n--- token totals ---")
    print(f"calls={u['calls']} prompt={u['prompt']} completion={u['completion']} "
          f"cached={u['cached']}  cache_hit_frac={u['cached']/u['prompt']:.2%}" if u['prompt'] else "no usage")
    print(f"final mem est. tokens = {estimate_memory_tokens(brain.memory)} "
          f"(should settle ≤ {brain.COMPACT_KEEP_TOKENS} after a trim)")


if __name__ == "__main__":
    asyncio.run(main())
