"""Chotu's brain — agent loop, memory buffer, terminal input."""

import asyncio
import json
import os
import time
import traceback
from collections import deque

from dotenv import load_dotenv

from chotu.llm_client import LLMClient
from chotu.pi_client import PiClient
from chotu.system_prompt import build_system_prompt
from chotu.tools import TOOL_SCHEMAS, build_dispatch, dispatch_tool, capture_vision_tool


# --- Config ---

load_dotenv()

PI_HOST = os.getenv("PI_HOST", "http://chotu.local:7000")
MODE = os.getenv("CHOTU_MODE", "A")
MAX_TOOL_ITERATIONS = 20
DEBUG = os.getenv("CHOTU_DEBUG", "0") == "1"
MUTE = os.getenv("CHOTU_MUTE", "0") == "1"
TICK_INTERVAL = int(os.getenv("CHOTU_TICK_INTERVAL", "5"))
VOICE_ENABLED = os.getenv("CHOTU_VOICE", "0") == "1"

listen_and_transcribe = None
if VOICE_ENABLED:
    from chotu.voice import listen_and_transcribe


# --- Globals ---

pi = PiClient(PI_HOST)
llm_client = LLMClient()
memory: deque = deque(maxlen=15)
input_queue: asyncio.Queue = asyncio.Queue()
OBSTACLE_CM = 15
estop: asyncio.Event = asyncio.Event()
object_map: dict = {}  # populated by scan_environment_tool; injected into context each turn


# --- Mute no-op ---

async def _muted_speak(**kw) -> dict:
    return {
        "ok": True, "tool": "speak",
        "result": {"text": kw.get("text", ""), "played": False, "muted": True},
        "duration_ms": 0, "timestamp": time.time(), "error": None,
    }


# --- Dispatch map ---

dispatch_map = build_dispatch(pi, estop)
if MUTE:
    dispatch_map["speak"] = lambda **kw: _muted_speak(**kw)


# --- scan_environment (local tool, not a Pi endpoint) ---

async def _describe_objects(image_b64: str) -> list[str]:
    """Mini LLM call to identify objects in a single image."""
    try:
        response = await llm_client.chat_complete(
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": "List visible objects. Comma-separated, one line, no articles. Be brief."},
            ]}],
            tools=[],
        )
        text = response.choices[0].message.content or ""
        return [o.strip() for o in text.split(",") if o.strip()]
    except Exception:
        return []


async def scan_environment_tool(segments: int = 8) -> dict:
    """360° sweep: rotate, photograph, identify objects at each position."""
    global object_map
    start = time.time()
    all_labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    labels = all_labels[:max(1, min(segments, 8))]
    entries = []

    for i, direction in enumerate(labels):
        if i > 0:
            turn = await pi.move("turn right", steps=1, speed=50)
            if not turn.get("ok"):
                break

        capture = await capture_vision_tool(pi)
        image_b64 = capture.get("result", {}).get("image_base64", "")
        objects = await _describe_objects(image_b64) if image_b64 else []
        entries.append({"direction": direction, "objects": objects})

    # Store map globally for context injection
    object_map = {e["direction"]: e["objects"] for e in entries}

    notable = [(e["direction"], obj) for e in entries for obj in e["objects"]]
    if notable:
        summary = "Found: " + ", ".join(f"{obj} ({d})" for d, obj in notable)
    else:
        summary = "No objects identified."

    ms = int((time.time() - start) * 1000)
    return {
        "ok": True, "tool": "scan_environment",
        "result": {"map": entries, "summary": summary},
        "duration_ms": ms, "timestamp": time.time(), "error": None,
    }


dispatch_map["scan_environment"] = lambda **kw: scan_environment_tool(**kw)


# --- Message building ---

def build_messages(user_input: str) -> list[dict]:
    sp = build_system_prompt(MODE)
    if object_map:
        sp += f"\n\n# Object map (from last scan)\n{json.dumps(object_map, indent=2)}"
    messages = [{"role": "system", "content": sp}]
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

def print_speak(text: str, muted: bool = False):
    label = "muted" if muted else "speaks"
    print(f'  [{label}] "{text}"')

def print_monologue(text: str):
    if text and text.strip():
        print(f"  [thinks] {text.strip()}")


# --- Obstacle poller ---

async def obstacle_poller(pi_client: PiClient, estop_event: asyncio.Event) -> None:
    while True:
        result = await pi_client.get_distance()
        if result.get("ok"):
            cm = result.get("result", {}).get("cm", 9999)
            if cm <= 0:
                pass
            elif cm < OBSTACLE_CM:
                if not estop_event.is_set():
                    dbg(f"[estop] obstacle at {cm:.1f}cm — movement blocked")
                estop_event.set()
            else:
                if estop_event.is_set():
                    dbg(f"[estop] clear ({cm:.1f}cm)")
                estop_event.clear()
        await asyncio.sleep(0.2)


# --- Mode B heartbeat ---

async def mode_b_tick() -> None:
    """Autonomous tick loop. Fires every TICK_INTERVAL seconds when MODE=B."""
    await asyncio.sleep(3.0)  # startup delay
    while True:
        await asyncio.sleep(TICK_INTERVAL)
        if not input_queue.empty():
            continue  # LLM still processing — don't pile up
        result = await pi.get_distance()
        if result.get("ok"):
            cm = result.get("result", {}).get("cm", -1)
            msg = f"[autonomous tick] distance: {cm:.1f}cm. Decide what to do."
        else:
            msg = "[autonomous tick] Sensor unavailable. Decide what to do."
        input_queue.put_nowait(msg)


# --- Brain loop ---

async def brain_loop():
    print(f"Chotu brain started (Mode {MODE}, model: {llm_client.model}, provider: {llm_client.provider})")
    if MUTE:
        print("  [mute] Audio disabled — speak() calls logged but not sent to Pi.")
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
    messages = build_messages(user_input)
    dbg(f"sending {len(messages)} messages to LLM")

    try:
        response = await llm_client.chat_complete(messages, TOOL_SCHEMAS)
    except Exception as e:
        print(f"  LLM error: {e}")
        return

    if not response.choices:
        print("  LLM error: empty choices")
        return

    iterations = 0
    while response.choices[0].message.tool_calls and iterations < MAX_TOOL_ITERATIONS:
        assistant_msg = response.choices[0].message
        messages.append(llm_client.format_assistant_message(response))

        deferred_vision = []
        results = await asyncio.gather(*[_run_one(tc) for tc in assistant_msg.tool_calls])

        for tool_call, name, args_json, result in results:
            try:
                args = json.loads(args_json) if args_json else {}
            except json.JSONDecodeError:
                args = {"_raw": args_json}

            print_tool_call(name, args, result)

            if name == "speak" and result.get("ok"):
                muted = result.get("result", {}).get("muted", False)
                print_speak(args.get("text", ""), muted=muted)

            if name == "capture_vision" and result.get("ok"):
                image_b64 = result["result"].get("image_base64", "")
                messages.append(llm_client.format_tool_result(tool_call.id, "Camera snapshot taken."))
                if image_b64:
                    deferred_vision.append({
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                            {"type": "text", "text": "This is your current camera view. Describe what you observe."},
                        ],
                    })
            else:
                messages.append(llm_client.format_tool_result(tool_call.id, json.dumps(result)))

        for msg in deferred_vision:
            messages.append(msg)

        dbg(f"follow-up LLM call (iteration {iterations + 1})")
        try:
            response = await llm_client.chat_complete(messages, TOOL_SCHEMAS)
        except Exception as e:
            print(f"  LLM error on follow-up: {e}")
            return

        if not response.choices:
            print("  LLM error: empty choices on follow-up")
            return

        iterations += 1

    if iterations >= MAX_TOOL_ITERATIONS:
        print("  [safety] Tool call limit reached, stopping.")

    final_text = response.choices[0].message.content
    print_monologue(final_text)

    memory.append({"role": "user", "content": user_input})
    if final_text:
        memory.append({"role": "assistant", "content": final_text})


# --- Input loops ---

async def input_loop():
    while True:
        try:
            text = await asyncio.to_thread(input, "you> ")
            input_queue.put_nowait(text)
        except EOFError:
            break


async def voice_loop():
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
    brain_task = asyncio.create_task(brain_loop())
    input_task = asyncio.create_task(voice_loop() if VOICE_ENABLED else input_loop())
    poller_task = asyncio.create_task(obstacle_poller(pi, estop))
    tasks = [brain_task, input_task, poller_task]

    if MODE == "B":
        tasks.append(asyncio.create_task(mode_b_tick()))

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as e:
        print(f"\n[fatal] Unhandled exception: {e}")
        traceback.print_exc()
    finally:
        for t in tasks:
            t.cancel()
        print("\nChotu sitting down...")
        try:
            await asyncio.wait_for(pi.pose("sit"), timeout=5.0)
        except Exception:
            pass
        print("Chotu shutting down. Bye!")


if __name__ == "__main__":
    asyncio.run(main())
