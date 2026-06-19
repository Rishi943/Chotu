"""Pure-text chat loop for benchmarking. No tools, no heartbeat, no Pi.

Usage:
    python -m core.chat.chat
    PALIV_LLM_PROVIDER=claude python -m core.chat.chat
"""

import asyncio
import time
from collections import deque

from dotenv import load_dotenv

from core.chat.prompt import CHAT_PROMPT
from core.llm_client import LLMClient

load_dotenv()

MAX_TURNS = 10


def _build_messages(memory: deque, user_input: str) -> list[dict]:
    messages = [{"role": "system", "content": CHAT_PROMPT}]
    messages.extend(memory)
    messages.append({"role": "user", "content": user_input})
    return messages


def _print_stats(stats: list[dict]) -> None:
    print("\n\x1b[1;34m--- session stats ---\x1b[0m")
    header = f"{'turn':>4}  {'prompt_tok':>10}  {'compl_tok':>9}  {'gen tok/s':>9}  {'wall_ms':>8}"
    print(header)
    print("-" * len(header))
    for s in stats:
        gen = f"{s['gen_tps']:.1f}" if s["gen_tps"] is not None else "  n/a"
        print(
            f"{s['turn']:>4}  {s['prompt_tokens']:>10}  {s['completion_tokens']:>9}"
            f"  {gen:>9}  {s['wall_ms']:>8.1f}"
        )
    print()


def _stats_summary(stats: list[dict]) -> str:
    lines = ["Session benchmark results:"]
    for s in stats:
        gen = f"{s['gen_tps']:.1f} tok/s" if s["gen_tps"] is not None else "n/a"
        lines.append(
            f"  Turn {s['turn']}: {s['prompt_tokens']} prompt / {s['completion_tokens']} compl tokens,"
            f" {gen}, {s['wall_ms']:.0f}ms wall"
        )
    return "\n".join(lines)


async def run_chat() -> None:
    llm = LLMClient()
    memory: deque = deque(maxlen=MAX_TURNS * 2)
    stats: list[dict] = []
    turn = 0

    print(f"Chat mode — model: {llm.model}. {MAX_TURNS} turns max. Ctrl+C to end early.\n")

    try:
        while turn < MAX_TURNS:
            try:
                user_input = await asyncio.to_thread(input, "\x1b[1;32myou>\x1b[0m ")
            except EOFError:
                break
            if not user_input.strip():
                continue

            turn += 1
            messages = _build_messages(memory, user_input)

            t0 = time.perf_counter()
            try:
                response = await llm.chat_complete(messages, tools=[])
            except Exception as e:
                print(f"  \x1b[31m[error]\x1b[0m LLM error: {e}")
                turn -= 1
                continue
            wall_ms = (time.perf_counter() - t0) * 1000

            msg = response.choices[0].message
            reply = msg.content or ""
            print(f"\x1b[1;35mchotu>\x1b[0m {reply}\n")

            usage = response.usage or {}
            gen_tps: float | None = None
            if "timings" in usage:
                gen_tps = usage["timings"].get("predicted_per_second")
            stats.append({
                "turn": turn,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "gen_tps": gen_tps,
                "wall_ms": wall_ms,
            })

            memory.append({"role": "user", "content": user_input})
            memory.append({"role": "assistant", "content": reply})

    except KeyboardInterrupt:
        print()

    if not stats:
        await llm.close()
        return

    _print_stats(stats)

    # Final acknowledgment — send raw stats back so Chotu can respond in character
    summary = _stats_summary(stats)
    ack_messages = _build_messages(memory, summary)
    try:
        ack_response = await llm.chat_complete(ack_messages, tools=[])
        ack_text = ack_response.choices[0].message.content or ""
        print(f"\x1b[1;35mchotu>\x1b[0m {ack_text}\n")
    except Exception as e:
        print(f"  \x1b[31m[error]\x1b[0m Could not get acknowledgment: {e}")

    await llm.close()


def main() -> None:
    asyncio.run(run_chat())


if __name__ == "__main__":
    main()
