"""Dry-run brain harness: real LLM, real system prompt, real tool-call loop — but every Pi call is faked.

Use when the Pi is offline/charging and you want to evaluate the LLM's personality + tool-call behaviour.

Usage:
    python -m scripts.dry_run
    python -m scripts.dry_run "walk forward 2 steps and say hi"

Output: tool calls (args + fake result), speak text, inner monologue. No Pi requests, no movement.
"""

import asyncio
import json
import os
import sys
import time
from collections import deque

from dotenv import load_dotenv
from openai import AsyncOpenAI

from chotu.system_prompt import build_system_prompt
from chotu.tools import TOOL_SCHEMAS

load_dotenv()

BRAIN_URL = os.getenv("CHOTU_BRAIN_URL", "http://localhost:8080/v1")
BRAIN_KEY = os.getenv("CHOTU_BRAIN_KEY", "not-needed")
BRAIN_MODEL = os.getenv("CHOTU_BRAIN_MODEL", "Qwen3.5-4B-Q4_K_M.gguf")
MODE = os.getenv("CHOTU_MODE", "A")
MAX_TOOL_ITERATIONS = 20

llm = AsyncOpenAI(base_url=BRAIN_URL, api_key=BRAIN_KEY, timeout=120.0)
memory: deque = deque(maxlen=15)


def fake_result(tool: str, args: dict) -> dict:
    """Return a plausible success envelope without touching the Pi."""
    base = {"ok": True, "tool": tool, "duration_ms": 0, "timestamp": time.time(), "error": None}
    if tool == "move":
        return {**base, "result": {
            "direction": args.get("direction", "forward"),
            "steps_requested": args.get("steps", 1),
            "steps_completed": args.get("steps", 1),
            "halted_early": False,
        }}
    if tool == "pose":
        return {**base, "result": {"pose": args.get("name", "stand"), "held_ms": 500}}
    if tool == "set_legs":
        return {**base, "result": {"legs": args.get("legs", []), "speed": args.get("speed", 50)}}
    if tool == "speak":
        return {**base, "result": {"text": args.get("text", ""), "played": True}}
    if tool == "get_distance":
        return {**base, "result": {"cm": 87.5, "reliable": True}}
    if tool == "get_battery":
        return {**base, "result": {"voltage": 7.6, "percent": 68, "charging": True}}
    if tool == "capture_vision":
        # No image in dry run — simulate the ack only; skip the deferred image.
        return {**base, "result": {"image_base64": "", "format": "jpeg"}}
    if tool == "wait":
        return {**base, "result": {"waited_seconds": args.get("seconds", 1), "reason": args.get("reason", "")}}
    return {**base, "ok": False, "result": {}, "error": f"unknown tool: {tool}"}


def print_tool_call(name: str, args: dict, result: dict):
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    status = "ok" if result.get("ok") else f"FAIL: {result.get('error')}"
    print(f"  [{name}] {args_str} -> {status}")
    if name == "speak" and result.get("ok"):
        print(f'    \x1b[36m[speaks]\x1b[0m "{args.get("text", "")}"')


def build_messages(user_input: str) -> list[dict]:
    messages = [{"role": "system", "content": build_system_prompt(MODE)}]
    for entry in memory:
        messages.append(entry)
    messages.append({"role": "user", "content": user_input})
    return messages


async def process(user_input: str):
    print(f"\n\x1b[1;33muser>\x1b[0m {user_input}")
    print("\x1b[90m--- chotu thinking ---\x1b[0m")

    messages = build_messages(user_input)

    try:
        response = await llm.chat.completions.create(
            model=BRAIN_MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
    except Exception as e:
        print(f"  LLM error: {e}")
        return

    iterations = 0
    while response.choices[0].message.tool_calls and iterations < MAX_TOOL_ITERATIONS:
        assistant_msg = response.choices[0].message
        msg_dict = {k: v for k, v in assistant_msg.model_dump().items() if v is not None}
        messages.append(msg_dict)

        # Dispatch all tool calls (fake) in "parallel" — order preserved.
        for tc in assistant_msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}

            result = fake_result(name, args)
            print_tool_call(name, args, result)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

        try:
            response = await llm.chat.completions.create(
                model=BRAIN_MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
        except Exception as e:
            print(f"  LLM error on follow-up: {e}")
            return

        iterations += 1

    if iterations >= MAX_TOOL_ITERATIONS:
        print("  \x1b[31m[safety] tool iteration limit hit\x1b[0m")

    final = response.choices[0].message.content or ""
    if final.strip():
        print(f"  \x1b[35m[thinks]\x1b[0m {final.strip()}")

    memory.append({"role": "user", "content": user_input})
    if final:
        memory.append({"role": "assistant", "content": final})


async def main():
    if len(sys.argv) > 1:
        await process(" ".join(sys.argv[1:]))
        return

    print(f"Dry-run brain (no Pi). Model: {BRAIN_MODEL}. Ctrl+C to quit.\n")
    while True:
        try:
            text = await asyncio.to_thread(input, "\x1b[1;32myou>\x1b[0m ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text.strip():
            continue
        await process(text)


if __name__ == "__main__":
    asyncio.run(main())
