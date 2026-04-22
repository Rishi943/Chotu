"""Chotu's brain — agent loop, memory buffer, terminal input."""

import asyncio
import json
import os
import traceback
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
MAX_TOOL_ITERATIONS = 20
DEBUG = os.getenv("CHOTU_DEBUG", "0") == "1"
VOICE_ENABLED = os.getenv("CHOTU_VOICE", "0") == "1"

listen_and_transcribe = None  # replaced below when VOICE_ENABLED
if VOICE_ENABLED:
    from chotu.voice import listen_and_transcribe


# --- Globals ---

pi = PiClient(PI_HOST)
llm = AsyncOpenAI(base_url=BRAIN_URL, api_key=BRAIN_KEY, timeout=60.0)
memory: deque = deque(maxlen=15)
input_queue: asyncio.Queue = asyncio.Queue()
OBSTACLE_CM = 15
estop: asyncio.Event = asyncio.Event()
dispatch_map = build_dispatch(pi, estop)


# --- Message building ---

def build_messages(user_input: str) -> list[dict]:
    """Build the full message list for the LLM from memory + new input."""
    messages = [{"role": "system", "content": build_system_prompt(MODE)}]
    for entry in memory:
        messages.append(entry)
    messages.append({"role": "user", "content": user_input})
    return messages


# --- Terminal output ---

def dbg(msg: str):
    if DEBUG:
        print(f"  [dbg] {msg}")

def print_tool_call(name: str, args: dict, result: dict):
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    ok = result.get("ok", False)
    ms = result.get("duration_ms", 0)
    status = "ok" if ok else f"FAIL: {result.get('error', '?')}"
    print(f"  [{name}] {args_str} -> {status} ({ms}ms)")

def print_speak(text: str):
    print(f'  [speaks] "{text}"')

def print_monologue(text: str):
    if text and text.strip():
        print(f"  [thinks] {text.strip()}")


# --- Obstacle poller ---

async def obstacle_poller(pi_client: PiClient, estop_event: asyncio.Event) -> None:
    """Poll distance sensor every 200ms. Set estop_event when obstacle < OBSTACLE_CM."""
    while True:
        result = await pi_client.get_distance()
        if result.get("ok"):
            cm = result.get("result", {}).get("cm", 9999)
            if cm <= 0:
                pass  # sensor returned invalid reading (e.g. -1.0), ignore
            elif cm < OBSTACLE_CM:
                if not estop_event.is_set():
                    dbg(f"[estop] obstacle at {cm:.1f}cm — movement blocked")
                estop_event.set()
            else:
                if estop_event.is_set():
                    dbg(f"[estop] clear ({cm:.1f}cm)")
                estop_event.clear()

        await asyncio.sleep(0.2)


# --- Brain loop ---

async def brain_loop():
    """Main agent loop. Waits for user input, runs LLM, dispatches tools."""
    print(f"Chotu brain started (Mode {MODE}, model: {BRAIN_MODEL})")
    if MODE == "C":
        print("WARNING: Mode C (WebSocket controller) is not implemented — falling back to Mode A behaviour.")
    print(f"Pi bridge: {PI_HOST}")

    health = await pi.health()
    if health.get("ok"):
        print("Pi bridge: connected")
    else:
        print(f"Pi bridge: NOT reachable ({health.get('error', '?')})")
        print("  Tools will return error envelopes. Continuing anyway.")

    print("Type a message to talk to Chotu. Ctrl+C to quit.\n")

    while True:
        user_input = await input_queue.get()
        if not user_input.strip():
            continue

        print(f"\n--- Chotu thinking ---")

        try:
            await _process(user_input)
        except Exception as e:
            print(f"  [brain error] {e}")
            traceback.print_exc()

        print()


async def _run_one(tc):
    name = tc.function.name
    args_json = tc.function.arguments
    dbg(f"dispatching {name}({args_json})")
    result = await dispatch_tool(dispatch_map, name, args_json)
    return tc, name, args_json, result


async def _process(user_input: str):
    """One full LLM activation: call → tool loop → final response."""
    messages = build_messages(user_input)

    dbg(f"sending {len(messages)} messages to LLM")
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

    if not response.choices:
        print("  LLM error: empty choices in response")
        return

    dbg(f"LLM responded: tool_calls={bool(response.choices[0].message.tool_calls)}, "
        f"content={bool(response.choices[0].message.content)}")

    # --- Tool call loop ---
    iterations = 0

    while response.choices[0].message.tool_calls and iterations < MAX_TOOL_ITERATIONS:
        assistant_msg = response.choices[0].message

        # Serialise assistant message, strip None fields (some llama.cpp builds reject them)
        msg_dict = {k: v for k, v in assistant_msg.model_dump().items() if v is not None}
        messages.append(msg_dict)

        deferred_vision = []

        # Dispatch all tool calls in parallel. gather preserves order.
        results = await asyncio.gather(*[_run_one(tc) for tc in assistant_msg.tool_calls])

        for tool_call, name, args_json, result in results:
            try:
                args = json.loads(args_json) if args_json else {}
            except json.JSONDecodeError:
                args = {"_raw": args_json}
            print_tool_call(name, args, result)

            if name == "speak" and result.get("ok"):
                print_speak(args.get("text", ""))

            # capture_vision: append tool ack now, defer the image user-message
            # until after ALL tool results — avoids invalid tool-after-user ordering
            if name == "capture_vision" and result.get("ok"):
                image_b64 = result["result"].get("image_base64", "")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": "Camera snapshot taken.",
                })
                if image_b64:
                    deferred_vision.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                            },
                            {
                                "type": "text",
                                "text": "This is your current camera view. Describe what you observe.",
                            },
                        ],
                    })
            else:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                })

        # Append vision images after all tool results for this turn
        for msg in deferred_vision:
            messages.append(msg)

        dbg(f"follow-up LLM call (iteration {iterations + 1})")
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

        if not response.choices:
            print("  LLM error: empty choices on follow-up")
            return

        dbg(f"follow-up responded: tool_calls={bool(response.choices[0].message.tool_calls)}")
        iterations += 1

    if iterations >= MAX_TOOL_ITERATIONS:
        print("  [safety] Tool call limit reached, stopping.")

    final_text = response.choices[0].message.content
    print_monologue(final_text)

    memory.append({"role": "user", "content": user_input})
    if final_text:
        memory.append({"role": "assistant", "content": final_text})


# --- Input loop ---

async def input_loop():
    """Read terminal input in a thread, push to queue."""
    while True:
        try:
            text = await asyncio.to_thread(input, "you> ")
            input_queue.put_nowait(text)
        except EOFError:
            break


async def voice_loop():
    """Wait for wake word, transcribe, push result to input_queue."""
    print("  [voice] Voice input active — say 'Hey Jarvis' to speak to Chotu.")
    while True:
        try:
            text = await listen_and_transcribe()
        except Exception as e:
            print(f"  [voice error] {e}")
            await asyncio.sleep(1.0)
            continue
        if text.strip():
            input_queue.put_nowait(text)


# --- Main ---

async def main():
    """Start brain, input, and obstacle poller loops."""
    brain_task = asyncio.create_task(brain_loop())
    if VOICE_ENABLED:
        input_task = asyncio.create_task(voice_loop())
    else:
        input_task = asyncio.create_task(input_loop())
    poller_task = asyncio.create_task(obstacle_poller(pi, estop))

    try:
        await asyncio.gather(brain_task, input_task, poller_task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as e:
        print(f"\n[fatal] Unhandled exception: {e}")
        traceback.print_exc()
    finally:
        brain_task.cancel()
        input_task.cancel()
        poller_task.cancel()
        print("\nChotu sitting down...")
        try:
            await asyncio.wait_for(pi.pose("sit"), timeout=5.0)
        except Exception:
            pass
        print("Chotu shutting down. Bye!")


if __name__ == "__main__":
    asyncio.run(main())
