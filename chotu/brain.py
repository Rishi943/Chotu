"""Chotu's brain — agent loop, memory buffer, terminal input."""

import asyncio
import json
import os
from collections import deque

from dotenv import load_dotenv
from openai import AsyncOpenAI

from chotu.pi_client import PiClient
from chotu.system_prompt import build_system_prompt
from chotu.tools import TOOL_SCHEMAS, build_dispatch, dispatch_tool


# --- Config ---

load_dotenv()

PI_HOST = os.getenv("PI_HOST", "http://chotu.local:7000")
BRAIN_URL = os.getenv("CHOTU_BRAIN_URL", "http://localhost:8080/v1")
BRAIN_KEY = os.getenv("CHOTU_BRAIN_KEY", "not-needed")
BRAIN_MODEL = os.getenv("CHOTU_BRAIN_MODEL", "qwen3.5-4b")
MODE = os.getenv("CHOTU_MODE", "A")
MAX_TOOL_ITERATIONS = 10


# --- Globals ---

pi = PiClient(PI_HOST)
llm = AsyncOpenAI(base_url=BRAIN_URL, api_key=BRAIN_KEY)
dispatch_map = build_dispatch(pi)
memory: deque = deque(maxlen=15)
input_queue: asyncio.Queue = asyncio.Queue()


# --- Message building ---

def build_messages(user_input: str) -> list[dict]:
    """Build the full message list for the LLM from memory + new input."""
    messages = [{"role": "system", "content": build_system_prompt(MODE)}]

    # Replay memory as conversation history
    for entry in memory:
        messages.append(entry)

    # Add new user input
    messages.append({"role": "user", "content": user_input})

    return messages


# --- Terminal output ---

def print_tool_call(name: str, args: dict, result: dict):
    """Pretty-print a tool call and its result."""
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    ok = result.get("ok", False)
    ms = result.get("duration_ms", 0)
    status = "ok" if ok else f"FAIL: {result.get('error', '?')}"
    print(f"  [{name}] {args_str} -> {status} ({ms}ms)")


def print_speak(text: str):
    """Highlight speak output."""
    print(f'  [speaks] "{text}"')


def print_monologue(text: str):
    """Print inner monologue."""
    if text and text.strip():
        print(f"  [thinks] {text.strip()}")


# --- Brain loop ---

async def brain_loop():
    """Main agent loop. Waits for user input, runs LLM, dispatches tools."""
    print(f"Chotu brain started (Mode {MODE}, model: {BRAIN_MODEL})")
    print(f"Pi bridge: {PI_HOST}")

    # Health check
    health = await pi.health()
    if health.get("ok"):
        print("Pi bridge: connected")
    else:
        print(f"Pi bridge: NOT reachable ({health.get('error', '?')})")
        print("  Tools will return error envelopes. Continuing anyway.\n")

    print("Type a message to talk to Chotu. Ctrl+C to quit.\n")

    while True:
        user_input = await input_queue.get()
        if not user_input.strip():
            continue

        print(f"\n--- Chotu thinking ---")

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
            continue

        # Tool call loop
        iterations = 0
        while response.choices[0].message.tool_calls and iterations < MAX_TOOL_ITERATIONS:
            assistant_msg = response.choices[0].message

            # Add assistant message (with tool calls) to messages.
            # Exclude None-valued fields that some llama.cpp builds reject.
            msg_dict = {k: v for k, v in assistant_msg.model_dump().items() if v is not None}
            messages.append(msg_dict)

            for tool_call in assistant_msg.tool_calls:
                name = tool_call.function.name
                args_json = tool_call.function.arguments

                # Dispatch
                result = await dispatch_tool(dispatch_map, name, args_json)

                # Print to terminal
                try:
                    args = json.loads(args_json) if args_json else {}
                except json.JSONDecodeError:
                    args = {"_raw": args_json}
                print_tool_call(name, args, result)

                # Highlight speak calls
                if name == "speak" and result.get("ok"):
                    print_speak(args.get("text", ""))

                # Add tool result to messages.
                # capture_vision: feed the raw image directly into the LLM context
                # so Qwen3.5 (multimodal) can analyse it natively.
                if name == "capture_vision" and result.get("ok"):
                    image_b64 = result["result"].get("image_base64", "")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": "Camera snapshot taken. Image follows in next message.",
                    })
                    if image_b64:
                        messages.append({
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                                },
                                {
                                    "type": "text",
                                    "text": "This is your current camera view. What do you observe?",
                                },
                            ],
                        })
                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result),
                    })

            # Next LLM turn
            try:
                response = await llm.chat.completions.create(
                    model=BRAIN_MODEL,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
            except Exception as e:
                print(f"  LLM error on follow-up: {e}")
                break

            iterations += 1

        if iterations >= MAX_TOOL_ITERATIONS:
            print("  [safety] Tool call limit reached, stopping.")

        # Final text response = inner monologue
        final_text = response.choices[0].message.content
        print_monologue(final_text)

        # Save to memory: user input and final assistant text.
        # Tool call details are within-activation context.
        # Between activations, the LLM only needs to know what was said.
        memory.append({"role": "user", "content": user_input})
        if final_text:
            memory.append({"role": "assistant", "content": final_text})

        print()  # blank line between interactions


# --- Input loop ---

async def input_loop():
    """Read terminal input in a thread, push to queue."""
    while True:
        try:
            text = await asyncio.to_thread(input, "you> ")
            input_queue.put_nowait(text)
        except EOFError:
            break


# --- Main ---

async def main():
    """Start brain and input loops."""
    brain_task = asyncio.create_task(brain_loop())
    input_task = asyncio.create_task(input_loop())

    try:
        await asyncio.gather(brain_task, input_task)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nChotu shutting down. Bye!")


if __name__ == "__main__":
    asyncio.run(main())
