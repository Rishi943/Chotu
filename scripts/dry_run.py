"""Dry-run brain harness: real LLM, real system prompt, real tool-call loop — but every Pi call is faked.

Usage:
    python -m scripts.dry_run
    python -m scripts.dry_run "walk forward 2 steps and say hi"
    PALIV_MUTE=1 python -m scripts.dry_run "hi"
"""

import asyncio
import json
import os
import sys
import time
from collections import deque

from dotenv import load_dotenv

from core.llm_client import LLMClient
from core.prompts import SYSTEM_PROMPT
from core.tools import TOOL_SCHEMAS

load_dotenv()

MUTE = os.getenv("PALIV_MUTE", "0") == "1"
MAX_TOOL_ITERATIONS = 6  # match brain.py live loop

llm_client = LLMClient()
memory: deque = deque(maxlen=15)


def fake_result(tool: str, args: dict) -> dict:
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
        return {**base, "result": {"legs": args.get("legs", []), "speed": args.get("speed", 80)}}
    if tool == "do_trick":
        return {**base, "result": {"name": args.get("name", "pushup")}}
    if tool == "get_distance":
        return {**base, "result": {"cm": 87.5, "reliable": True}}
    if tool == "get_battery":
        return {**base, "result": {"voltage": 7.6, "percent": 68, "charging": True}}
    if tool == "capture_vision":
        return {**base, "result": {"image_base64": "", "format": "jpeg"}}
    if tool == "get_perception":
        color = args.get("color")
        result = {}
        if color:
            result["color"] = {"target": color, "detected": False, "x": 0, "y": 0, "size": 0}
        if args.get("face"):
            result["face"] = {"detected": False, "x": 0, "y": 0}
        if args.get("human"):
            result["human"] = {"detected": False}
        return {**base, "result": result}
    if tool == "wait":
        return {**base, "result": {"waited_seconds": args.get("seconds", 1), "reason": args.get("reason", "")}}
    if tool == "set_face":
        return {**base, "result": {"name": args.get("name", "idle"), "ok": True}}
    if tool == "cast_spell":
        return {**base, "result": {"spell": args.get("name", "")}}
    return {**base, "ok": False, "result": {}, "error": f"unknown tool: {tool}"}


def print_tool_call(name: str, args: dict, result: dict):
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    status = "ok" if result.get("ok") else f"FAIL: {result.get('error')}"
    print(f"  [{name}] {args_str} -> {status}")


def print_speak(text: str):
    if not text or not text.strip():
        return
    label = "muted" if MUTE else "speaks"
    print(f'  \x1b[36m[{label}]\x1b[0m "{text.strip()}"')


def build_messages(user_input: str) -> list[dict]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for entry in memory:
        messages.append(entry)
    messages.append({"role": "user", "content": user_input})
    return messages


async def process(user_input: str):
    print(f"\n\x1b[1;33muser>\x1b[0m {user_input}")
    print("\x1b[90m--- chotu thinking ---\x1b[0m")

    messages = build_messages(user_input)
    try:
        response = await llm_client.chat_complete(messages, TOOL_SCHEMAS)
    except Exception as e:
        print(f"  LLM error: {e}")
        return

    # Speak from initial response content
    print_speak(response.choices[0].message.content or "")

    iterations = 0
    while response.choices[0].message.tool_calls and iterations < MAX_TOOL_ITERATIONS:
        assistant_msg = response.choices[0].message
        messages.append(llm_client.format_assistant_message(response))

        for tc in assistant_msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}

            result = fake_result(name, args)
            print_tool_call(name, args, result)
            messages.append(llm_client.format_tool_result(tc.id, json.dumps(result)))

        try:
            response = await llm_client.chat_complete(messages, TOOL_SCHEMAS)
        except Exception as e:
            print(f"  LLM error on follow-up: {e}")
            return
        # Speak from follow-up response content
        print_speak(response.choices[0].message.content or "")
        iterations += 1

    if iterations >= MAX_TOOL_ITERATIONS:
        print("  \x1b[31m[safety] tool iteration limit hit\x1b[0m")

    final = response.choices[0].message.content or ""

    memory.append({"role": "user", "content": user_input})
    if final:
        memory.append({"role": "assistant", "content": final})


async def main():
    if len(sys.argv) > 1:
        await process(" ".join(sys.argv[1:]))
        return
    print(f"Dry-run brain (no Pi). Model: {llm_client.model}. Ctrl+C to quit.\n")
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
