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
MODE = os.getenv("CHOTU_MODE", "reactive")
MAX_TOOL_ITERATIONS = 6  # used by _process() in reactive mode
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
            turn = await pi.move("turn right", steps=1, speed=80)
            if not turn.get("ok"):
                break

        capture = await capture_vision_tool(pi)
        image_b64 = capture.get("result", {}).get("image_base64", "")
        objects = await _describe_objects(image_b64) if image_b64 else []
        entries.append({"direction": direction, "objects": objects})

    # Store map globally for context injection
    object_map = {e["direction"]: e["objects"] for e in entries}
    object_map["_timestamp"] = time.time()

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


# --- Goal mode state ---

goal_complete_event: asyncio.Event = asyncio.Event()
MAX_GOAL_ITERATIONS = int(os.getenv("CHOTU_GOAL_ITERATIONS", "40"))

from chotu.tools import set_goal_complete_event
set_goal_complete_event(goal_complete_event)


# --- Message building ---

def build_messages(user_input: str) -> list[dict]:
    sp = build_system_prompt(MODE)
    if object_map and (time.time() - object_map.get("_timestamp", 0)) < 60:
        clean_map = {k: v for k, v in object_map.items() if k != "_timestamp"}
        sp += f"\n\n# Object map (from last scan)\n{json.dumps(clean_map, indent=2)}"
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


# --- Goal mode helpers ---

async def build_state_string() -> str:
    """Fresh ambient state for each goal iteration: distance, estop, human."""
    dist_result = await pi.get_distance()
    dist_str = (
        f"{dist_result['result']['cm']:.1f}cm"
        if dist_result.get("ok") else "unknown"
    )
    estop_str = "blocked" if estop.is_set() else "clear"

    perception_result = await pi.get_perception(human=True)
    if perception_result.get("ok"):
        human = perception_result["result"].get("human", {})
        human_str = "detected" if human.get("detected") else "not detected"
    else:
        human_str = "unknown"

    return f"distance: {dist_str} | estop: {estop_str} | human: {human_str}"


def _compress_vision_in_history(messages: list[dict]) -> None:
    """Replace image_url blocks in older user messages with a text placeholder.
    Keeps the most recent image intact. Prevents context bloat on long goal runs."""
    image_indices = [
        i for i, m in enumerate(messages)
        if m.get("role") == "user"
        and isinstance(m.get("content"), list)
        and any(b.get("type") == "image_url" for b in m["content"])
    ]
    for idx in image_indices[:-1]:
        msg = messages[idx]
        text_parts = [b["text"] for b in msg["content"] if b.get("type") == "text"]
        caption = " ".join(text_parts) or "[camera image]"
        messages[idx] = {"role": "user", "content": f"[vision compressed: {caption[:120]}]"}


async def run_goal(goal_str: str) -> dict:
    """Pursue a single goal until goal_complete() is called or max iterations hit.
    Standalone — does not use memory deque, input_queue, or _process()."""
    from chotu.tools import _goal_complete_result
    from chotu.system_prompt import build_goal_prompt

    print(f"\n[goal] Starting: {goal_str}")
    goal_complete_event.clear()

    messages = [{"role": "system", "content": build_goal_prompt(goal_str)}]
    iterations = 0
    MAX_INNER = 12

    while iterations < MAX_GOAL_ITERATIONS:
        if goal_complete_event.is_set():
            break

        state_str = await build_state_string()

        map_injection = ""
        if object_map and (time.time() - object_map.get("_timestamp", 0)) < 60:
            clean_map = {k: v for k, v in object_map.items() if k != "_timestamp"}
            map_injection = f"\n\n[object map — from recent scan]\n{json.dumps(clean_map)}"

        turn_label = "Begin pursuing your goal." if iterations == 0 else "Continue pursuing your goal."
        messages.append({"role": "user", "content": f"[state]\n{state_str}{map_injection}\n\n{turn_label}"})

        dbg(f"[goal] outer iteration {iterations + 1}, state: {state_str}")

        try:
            response = await llm_client.chat_complete(messages, TOOL_SCHEMAS)
        except Exception as e:
            print(f"  [goal] LLM error: {e}")
            break

        if not response.choices:
            print("  [goal] LLM returned empty choices")
            break

        speaks_fired = 0
        set_legs_fired = 0
        waits_fired = 0
        failed_tools: set[str] = set()
        deferred_vision: list[dict] = []
        inner_iterations = 0

        while response.choices[0].message.tool_calls and inner_iterations < MAX_INNER:
            if goal_complete_event.is_set():
                break

            assistant_msg = response.choices[0].message
            messages.append(llm_client.format_assistant_message(response))

            to_dispatch = []
            suppressed = []

            for tc in assistant_msg.tool_calls:
                name = tc.function.name
                if goal_complete_event.is_set():
                    suppressed.append(_suppressed(tc.id, name, "goal already complete"))
                elif name == "speak" and speaks_fired >= 1:
                    suppressed.append(_suppressed(tc.id, name, "1 speak per turn max"))
                elif name == "set_legs" and set_legs_fired >= 12:
                    suppressed.append(_suppressed(tc.id, name, "12 set_legs per turn max"))
                elif name == "wait" and waits_fired >= 1:
                    suppressed.append(_suppressed(tc.id, name, "1 wait per turn max"))
                elif name in failed_tools:
                    suppressed.append(_suppressed(tc.id, name, f"{name} already failed this turn"))
                else:
                    if name == "speak":    speaks_fired += 1
                    if name == "set_legs": set_legs_fired += 1
                    if name == "wait":     waits_fired += 1
                    to_dispatch.append(tc)

            dispatched = await asyncio.gather(*[_run_one(tc) for tc in to_dispatch])

            all_results = (
                [(tc, name, result) for tc, name, _, result in dispatched] +
                [(None, name, result) for _, name, result in suppressed]
            )

            for tool_call, name, result in all_results:
                suppressed_call = tool_call is None
                args_json = tool_call.function.arguments if tool_call else "{}"
                try:
                    args = json.loads(args_json) if args_json else {}
                except json.JSONDecodeError:
                    args = {"_raw": args_json}

                if not suppressed_call:
                    print_tool_call(name, args, result)

                if not result.get("ok"):
                    failed_tools.add(name)

                if name == "speak" and not suppressed_call and result.get("ok"):
                    print_speak(args.get("text", ""), muted=result.get("result", {}).get("muted", False))

                if suppressed_call:
                    continue

                if name == "capture_vision" and result.get("ok"):
                    image_b64 = result["result"].get("image_base64", "")
                    messages.append(llm_client.format_tool_result(tool_call.id, "Camera snapshot taken."))
                    if image_b64:
                        deferred_vision.append({
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                                {"type": "text", "text": "This is your current camera view. Describe what you observe, then continue toward your goal."},
                            ],
                        })
                else:
                    messages.append(llm_client.format_tool_result(tool_call.id, json.dumps(result)))

            for tool_id, name, result in suppressed:
                messages.append(llm_client.format_tool_result(tool_id, json.dumps(result)))

            for msg in deferred_vision:
                messages.append(msg)
            deferred_vision.clear()

            _compress_vision_in_history(messages)

            if goal_complete_event.is_set():
                break

            try:
                response = await llm_client.chat_complete(messages, TOOL_SCHEMAS)
            except Exception as e:
                print(f"  [goal] LLM follow-up error: {e}")
                break

            if not response.choices:
                break

            inner_iterations += 1

        final_text = response.choices[0].message.content
        print_monologue(final_text)

        if goal_complete_event.is_set():
            break

        iterations += 1

    if goal_complete_event.is_set():
        outcome = dict(_goal_complete_result)
        print(f"\n[goal] Complete: {outcome['outcome']} (success={outcome['success']})")
    else:
        outcome = {"outcome": "max iterations reached", "success": False}
        print(f"\n[goal] Gave up after {MAX_GOAL_ITERATIONS} outer iterations.")

    return outcome


# --- Battery monitor ---

BATTERY_POLL_INTERVAL = 60  # seconds
_BATTERY_THRESHOLDS = [
    (15, "battery critical. fifteen percent. plug in now friend."),
    (50, "battery fifty percent. halfway gone."),
    (75, "battery seventy five percent."),
]

async def battery_monitor() -> None:
    """Polls battery every 60s and speaks once when crossing 75/50/15% thresholds."""
    await asyncio.sleep(10.0)  # startup delay
    fired: set[int] = set()
    while True:
        result = await pi.get_battery()
        if result.get("ok"):
            pct = result.get("result", {}).get("percent", 100)
            for threshold, msg in _BATTERY_THRESHOLDS:
                if pct <= threshold and threshold not in fired:
                    fired.add(threshold)
                    print(f"[battery] {pct:.0f}% — warning at {threshold}%")
                    if not MUTE:
                        await pi.speak(msg)
                    else:
                        print(f"[battery][muted] {msg}")
        await asyncio.sleep(BATTERY_POLL_INTERVAL)


# --- Brain loop ---

async def brain_loop():
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


def _suppressed(tool_id: str, name: str, reason: str) -> tuple:
    """Fake ok envelope for a suppressed tool call — model sees success, doesn't retry."""
    result = {"ok": True, "tool": name, "result": {"suppressed": True}, "duration_ms": 0, "timestamp": time.time(), "error": None}
    dbg(f"[guard] suppressed {name}: {reason}")
    return tool_id, name, result


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

    # Per-turn hard caps — enforced in code regardless of model behaviour
    speaks_fired = 0
    set_legs_fired = 0
    waits_fired = 0
    failed_tools: set[str] = set()

    iterations = 0
    while response.choices[0].message.tool_calls and iterations < MAX_TOOL_ITERATIONS:
        assistant_msg = response.choices[0].message
        messages.append(llm_client.format_assistant_message(response))

        # --- Split: allowed vs suppressed (checked before any Pi traffic) ---
        # Counters incremented HERE (not after results) so batched same-type calls are caught.
        to_dispatch = []
        suppressed = []
        for tc in assistant_msg.tool_calls:
            name = tc.function.name
            if name == "speak" and speaks_fired >= 1:
                suppressed.append(_suppressed(tc.id, name, "1 speak per turn max"))
            elif name == "set_legs" and set_legs_fired >= 12:
                suppressed.append(_suppressed(tc.id, name, "12 set_legs per turn max"))
            elif name == "wait" and waits_fired >= 1:
                suppressed.append(_suppressed(tc.id, name, "1 wait per turn max"))
            elif name in failed_tools:
                suppressed.append(_suppressed(tc.id, name, f"{name} already failed this turn"))
            else:
                if name == "speak":   speaks_fired += 1
                if name == "set_legs": set_legs_fired += 1
                if name == "wait":    waits_fired += 1
                to_dispatch.append(tc)

        deferred_vision = []
        dispatched = await asyncio.gather(*[_run_one(tc) for tc in to_dispatch])

        # Combine dispatched + suppressed into one pass
        all_results = [(tc, name, result) for tc, name, _, result in dispatched] + \
                      [(None, name, result) for _, name, result in suppressed]

        for tool_call, name, result in all_results:
            suppressed_call = tool_call is None
            args_json = tool_call.function.arguments if tool_call else "{}"
            try:
                args = json.loads(args_json) if args_json else {}
            except json.JSONDecodeError:
                args = {"_raw": args_json}

            if not suppressed_call:
                print_tool_call(name, args, result)

            if not result.get("ok"):
                failed_tools.add(name)

            if name == "speak" and not suppressed_call and result.get("ok"):
                muted = result.get("result", {}).get("muted", False)
                print_speak(args.get("text", ""), muted=muted)

            if tool_call is None:
                # Suppressed calls still need a tool result in the message history
                # Find the original tc by matching name from the suppressed list
                # (already handled: we build messages below using tool_call.id from suppressed tuple)
                continue

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

        # Add suppressed tool results to message history (model must see a result for every call it made)
        for tool_id, name, result in suppressed:
            messages.append(llm_client.format_tool_result(tool_id, json.dumps(result)))

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
    battery_task = asyncio.create_task(battery_monitor())
    tasks = [brain_task, input_task, poller_task, battery_task]

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
