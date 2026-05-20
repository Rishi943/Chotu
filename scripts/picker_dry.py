"""Dry-run harness for core.picker.

Examples:
  python -m scripts.picker_dry
  python -m scripts.picker_dry --simulate 50
  python -m scripts.picker_dry --simulate 20 --seed-history "do_nothing,do_nothing,do_nothing"
  python -m scripts.picker_dry --simulate 20 --start-state play
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter

from core.llm_client import LLMClient
from core.picker import PickerInput, pick_next


class _FallbackCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage().startswith("picker fallback:"):
            self.count += 1


HISTORY_MAX = 5


def _push(history: list[str], pick_name: str) -> list[str]:
    h = (history + [pick_name])[-HISTORY_MAX:]
    return h


async def _interactive(llm: LLMClient) -> None:
    print("picker_dry — interactive. Ctrl-C to quit.")
    history: list[str] = []
    state = "idle"
    while True:
        raw = input(f"\n[state={state}, history={history}] press enter to pick (or type 'state=play'/'state=idle' to switch): ").strip()
        if raw.startswith("state="):
            new = raw.split("=", 1)[1].strip()
            if new in ("idle", "play"):
                state = new
                continue
            print(f"unknown state: {new!r}")
            continue
        pick = await pick_next(PickerInput(current_state=state, recent_picks=history), llm)
        print(f"  -> {pick}")
        history = _push(history, pick.name)
        state = pick.state


async def _simulate(llm: LLMClient, n: int, history: list[str], state: str, fallback_counter: _FallbackCounter) -> int:
    counts: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    fallback_streak = 0
    max_fallback_streak = 0
    fallback_total = 0

    for i in range(n):
        prev_state = state
        before = fallback_counter.count
        pick = await pick_next(PickerInput(current_state=state, recent_picks=history), llm)
        is_fallback = fallback_counter.count > before
        if is_fallback:
            fallback_streak += 1
            fallback_total += 1
            max_fallback_streak = max(max_fallback_streak, fallback_streak)
        else:
            fallback_streak = 0
        counts[f"{pick.state}:{pick.name}"] += 1
        if pick.state != prev_state:
            transitions[f"{prev_state}->{pick.state}"] += 1
        print(f"[{i+1:>3}/{n}] state={prev_state} hist={history} -> {pick}")
        history = _push(history, pick.name)
        state = pick.state

    print("\n--- histogram ---")
    for key, count in counts.most_common():
        print(f"  {key:<30} {count}")
    print("\n--- transitions ---")
    for key, count in transitions.most_common():
        print(f"  {key:<30} {count}")
    print(f"\nfallback picks: {fallback_total}/{n}  (max consecutive streak: {max_fallback_streak})")

    if fallback_total > n // 2:
        print("FAIL: more than half of picks were fallbacks — picker is not working.", file=sys.stderr)
        return 1
    return 0


def _parse_history(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()][-HISTORY_MAX:]


async def main() -> int:
    parser = argparse.ArgumentParser(description="Picker dry-run harness")
    parser.add_argument("--simulate", type=int, default=None, help="run N picks non-interactively")
    parser.add_argument("--seed-history", type=str, default=None, help="comma-separated initial history, oldest first")
    parser.add_argument("--start-state", choices=("idle", "play"), default="idle")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    fallback_counter = _FallbackCounter()
    logging.getLogger("core.picker").addHandler(fallback_counter)

    llm = LLMClient()
    try:
        if args.simulate is not None:
            return await _simulate(llm, args.simulate, _parse_history(args.seed_history), args.start_state, fallback_counter)
        await _interactive(llm)
        return 0
    finally:
        await llm.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nbye.")
