#!/usr/bin/env python3
"""Live habit tester — runs one named habit against the real Pi.

Usage:
    python scripts/test_habits_live.py <habit_name>
    python scripts/test_habits_live.py --all     # runs all in sequence with pause between

Examples:
    python scripts/test_habits_live.py yawn
    python scripts/test_habits_live.py pushup
    python scripts/test_habits_live.py --all
"""

import asyncio
import logging
import sys

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from core.habits import IDLE_HABIT_MAP, run_habit
from core.pi_client import PiClient

import os
PI_URL = os.getenv("PI_URL", "http://chotu.local:7000")

HABIT_ORDER = list(IDLE_HABIT_MAP.keys())


async def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <habit_name> | --all")
        print(f"Available: {', '.join(HABIT_ORDER)}")
        sys.exit(1)

    pi = PiClient(PI_URL)

    try:
        health = await pi.health()
        if not health.get("ok"):
            print(f"Pi bridge not reachable at {PI_URL}. Start it first.")
            sys.exit(1)
        print(f"Pi bridge OK at {PI_URL}")

        if sys.argv[1] == "--all":
            for name in HABIT_ORDER:
                print(f"\n--- Running: {name} ---")
                input("Press Enter to run, Ctrl+C to stop...")
                await run_habit(name, pi)
                print(f"Done: {name}")
        else:
            name = sys.argv[1]
            if name not in IDLE_HABIT_MAP:
                print(f"Unknown habit: {name!r}. Available: {', '.join(HABIT_ORDER)}")
                sys.exit(1)
            print(f"Running habit: {name}")
            await run_habit(name, pi)
            print("Done.")
    finally:
        await pi.close()


if __name__ == "__main__":
    asyncio.run(main())
