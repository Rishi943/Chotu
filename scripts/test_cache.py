"""Verify DashScope explicit context-cache hits for the real system prompt.

Makes two back-to-back calls with the SAME system prompt + tool schemas (exactly
what the loop sends). The cache_control:ephemeral marker added by llm_client should
make the 2nd call report cached_tokens > 0.

The marker is only applied when PALIV_BRAIN_URL points at DashScope, so run against
your cloud config — either set PALIV_BRAIN_URL/KEY/MODEL in the env first, or use
--dashscope to point at the intl endpoint with DASHSCOPE_API_KEY:

    PALIV_BRAIN_URL=... PALIV_BRAIN_KEY=... PALIV_BRAIN_MODEL=qwen3.5-flash \
        python -m scripts.test_cache
    python -m scripts.test_cache --dashscope qwen3.5-flash      # uses DASHSCOPE_API_KEY
    python -m scripts.test_cache --dashscope qwen3.5-flash -n 4 # more calls
"""

import argparse
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from core.llm_client import LLMClient
from core.prompts import SYSTEM_PROMPT
from core.tools import TOOL_SCHEMAS


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dashscope", metavar="MODEL",
                    help="Point at DashScope intl with DASHSCOPE_API_KEY.")
    ap.add_argument("-n", type=int, default=3, help="Number of calls (default 3).")
    ap.add_argument("--moving", action="store_true",
                    help="Simulate the loop: grow memory each call, mark system + "
                         "end-of-memory breakpoint, report cached_tokens per call (O-1).")
    args = ap.parse_args()

    if args.dashscope:
        os.environ["PALIV_LLM_PROVIDER"] = "local"  # DashScope is OpenAI-compatible
        os.environ["PALIV_BRAIN_URL"] = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        os.environ["PALIV_BRAIN_KEY"] = os.environ["DASHSCOPE_API_KEY"]
        os.environ["PALIV_BRAIN_MODEL"] = args.dashscope

    llm = LLMClient()
    print(f"model={llm.model}  cache_markers_on={llm._cache_system}")
    if not llm._cache_system:
        print("WARNING: PALIV_BRAIN_URL is not DashScope — no cache marker will be sent.")
    print("-" * 64)

    if args.moving:
        from core.brain import build_loop_messages
        from core.scratchpad import Scratchpad
        memory: list[dict] = []
        for i in range(args.n):
            # append one synthetic prior turn (assistant + tool result) each call
            memory.append({"role": "assistant", "content": f"observation {i}: nothing new"})
            memory.append({"role": "tool", "tool_call_id": str(i),
                           "content": '{"ok": true, "result": {"distance_cm": -1}}'})
            messages = build_loop_messages(SYSTEM_PROMPT, memory, [], Scratchpad(),
                                           cache_boundary=llm.supports_cache_control)
            r = await llm.chat_complete(messages, TOOL_SCHEMAS, max_tokens=16)
            u = r.usage or {}
            p, cached = u.get("prompt_tokens", 0), u.get("cached_tokens", 0)
            pct = f"{cached / p * 100:.0f}%" if p else "—"
            flag = "  <-- CACHE HIT" if cached else ""
            print(f"  call {i}: prompt={p}  cached={cached} ({pct})  mem_msgs={len(memory)}{flag}")
        print("-" * 64)
        print("O-1: cached should GROW with the prefix (proves the moving end-of-memory")
        print("breakpoint, not just a fixed system block). If cached plateaus at the")
        print("system-only size, DashScope is honoring 1 breakpoint — flip to 1 marker.")
        print("If call 0 errors on the tool-role block, apply the §B assistant/user fallback.")
        await llm.close()
        return

    # Same stable prefix every call (system prompt + tools), tiny varying user turn.
    for i in range(args.n):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"reply with one word (call {i})"},
        ]
        r = await llm.chat_complete(messages, TOOL_SCHEMAS, max_tokens=16)
        u = r.usage or {}
        p = u.get("prompt_tokens", 0)
        cached = u.get("cached_tokens", 0)
        pct = f"{cached / p * 100:.0f}%" if p else "—"
        flag = "  <-- CACHE HIT" if cached else ""
        print(f"  call {i}: prompt={p}  cached={cached} ({pct}){flag}")

    print("-" * 64)
    print("Expect cached=0 on call 0 (creates cache), then cached>0 from call 1 on.")
    print("If cached stays 0: explicit cache isn't landing for this model/region.")
    await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
