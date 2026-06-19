"""Token budget for a 1-FPS vision loop, measured against the LOCAL model.

Reports, using the model's own reported prompt_tokens (exact, not char/4):

  1. The CACHEABLE PREFIX sent on every call:
       - system prompt (PALIV.md + CHOTU_BASE.md)
       - tool schemas (tools are part of the prompt)
  2. One real camera frame's cost.
  3. A simulated 1-FPS loop (heartbeat -> capture_vision -> frame -> reply),
     showing the FULL context size and the per-turn delta for turns 1..N.
     Only the newest FRAME_WINDOW turns keep their image; older turns are
     stripped to a text stub, mirroring enforce_frame_window in brain.py.

Local only — text tokenizes within a few % across these models, fine for an estimate.

Usage:
    python -m scripts.bench.measure_fps_budget            # 4 turns, live Pi frame
    python -m scripts.bench.measure_fps_budget --turns 6
"""

import argparse
import asyncio
import base64
import json
import os

from dotenv import load_dotenv

load_dotenv()

from core.llm_client import LLMClient
from core.pi_client import PiClient
from core.prompts import SYSTEM_PROMPT
from core.tools import TOOL_SCHEMAS

PI_HOST = os.getenv("PI_HOST", "http://chotu.local:7000")
_MAX_TOK = 16


async def _tok(llm: LLMClient, messages: list[dict], tools: list[dict]) -> int:
    resp = await llm.chat_complete(messages, tools=tools, thinking=False, max_tokens=_MAX_TOK)
    if not resp.usage or "prompt_tokens" not in resp.usage:
        raise SystemExit("provider returned no usage.prompt_tokens")
    return resp.usage["prompt_tokens"]


async def _get_frame_b64() -> str:
    pi = PiClient(PI_HOST)
    try:
        env = await pi.capture()
    finally:
        await pi.close()
    if not env.get("ok"):
        raise SystemExit(f"Pi /capture failed: {env.get('error')}")
    return env.get("result", {}).get("image_base64", "")


def _frame_msg(b64: str) -> dict:
    # Exactly how core/brain.py appends a captured frame (deferred vision).
    return {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": "This is your current camera view. Describe what you observe."},
    ]}


def _turn_block(b64: str, with_frame: bool) -> list[dict]:
    """One persisted turn. `with_frame` carries the image; stripped (older) turns
    keep only the text stub, mirroring enforce_frame_window."""
    frame_part = _frame_msg(b64) if with_frame else {
        "role": "user", "content": "[earlier camera frame — see description below]"}
    return [
        {"role": "user", "content": "[heartbeat]"},
        {"role": "assistant", "content": "*Scanning the room.*",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "capture_vision", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "Camera snapshot taken."},
        frame_part,
        {"role": "assistant", "content": "A desk, a monitor, my charging cable on the floor.",
         "tool_calls": [{"id": "c2", "type": "function",
                         "function": {"name": "speak",
                                      "arguments": json.dumps({"text": "I see my charging cable."})}}]},
        {"role": "tool", "tool_call_id": "c2",
         "content": json.dumps({"ok": True, "tool": "speak", "result": {"played": True},
                                "duration_ms": 1200, "timestamp": 0, "error": None})},
    ]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=4)
    args = ap.parse_args()

    b64 = await _get_frame_b64()
    llm = LLMClient()
    print(f"provider={llm.provider}  model={llm.model}")
    print(f"frame jpeg=~{len(base64.b64decode(b64))/1024:.1f}KB\n")

    sys_msg = {"role": "system", "content": SYSTEM_PROMPT}
    tiny = {"role": "user", "content": "x"}

    # --- 1. cacheable prefix breakdown (diff method) ---
    base = await _tok(llm, [tiny], [])                       # chat template + "x"
    with_sys = await _tok(llm, [sys_msg, tiny], [])          # + system
    with_tools = await _tok(llm, [sys_msg, tiny], TOOL_SCHEMAS)  # + tools
    sys_tok = with_sys - base
    tools_tok = with_tools - with_sys
    prefix = sys_tok + tools_tok

    # one frame in isolation
    frame_only = await _tok(llm, [sys_msg, tiny, _frame_msg(b64)], []) - with_sys

    print("CACHEABLE PREFIX (sent every call, KV-cache reused after turn 1)")
    print(f"  system prompt (PALIV.md + CHOTU_BASE.md) : {sys_tok:>6} tok")
    print(f"  tool schemas (13 tools)                  : {tools_tok:>6} tok")
    print(f"  --------------------------------------------------")
    print(f"  prefix total                             : {prefix:>6} tok")
    print(f"\nONE CAMERA FRAME                           : {frame_only:>6} tok\n")

    # --- 2. simulated 1-FPS loop ---
    from core.brain import FRAME_WINDOW
    print(f"1-FPS LOOP — full context per turn (frame window = {FRAME_WINDOW}):\n")
    print(f"  {'turn':<6}{'full input':>12}{'delta':>10}{'images kept':>14}")
    memory: list[dict] = []
    prev = 0
    n_blocks = 0
    for t in range(1, args.turns + 1):
        msgs = [sys_msg] + memory + [{"role": "user", "content": "[heartbeat]"}]
        full = await _tok(llm, msgs, TOOL_SCHEMAS)
        imgs = min(t - 1, FRAME_WINDOW)
        print(f"  {t:<6}{full:>12}{full - prev:>10}{imgs:>14}")
        prev = full
        # this turn's exchange lands in memory; then frames older than the window are stripped
        n_blocks += 1
        keep_from = max(0, n_blocks - FRAME_WINDOW)
        memory = []
        for j in range(n_blocks):
            memory += _turn_block(b64, with_frame=(j >= keep_from))

    await llm.close()
    print("\nnote: 'full input' is what the model ingests that turn. With prompt")
    print("caching the prefix + unchanged history is cache-read; 'delta' is the")
    print("fresh work each second (new heartbeat + last turn's reply + a frame).")


if __name__ == "__main__":
    asyncio.run(main())
