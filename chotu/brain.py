"""Chotu's brain — agent loop, memory buffer, terminal input."""

import asyncio
import json
import os
import re
import signal
import time
import traceback
from collections import deque

from dotenv import load_dotenv

from chotu.llm_client import LLMClient
from chotu.pi_client import PiClient
from chotu.system_prompt import build_system_prompt
from chotu.tools import TOOL_SCHEMAS, GOAL_TOOL_SCHEMAS, build_dispatch, dispatch_tool, capture_vision_tool


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
gui_event_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
gallery_store: list[dict] = []
thinking_enabled: bool = False
_active_goal_task: asyncio.Task | None = None
_pi_reachable: bool = False

continuous_mode: bool = False
tts_done_event: asyncio.Event = asyncio.Event()
tts_done_event.set()  # initially ready — no TTS playing at startup
_pending_speaks: int = 0
_pi_reachable: bool = False

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


# --- Speak firing (from message content, not tool call) ---

def _fire_face(name: str) -> None:
    if _pi_reachable:
        asyncio.create_task(pi.set_face(name=name))


def _fire_speak_if_content(content: str | None) -> asyncio.Task | None:
    """If content is non-empty, fire local_speak as a background task. Returns the task or None."""
    if not content or not content.strip():
        return None
    text = content.strip()
    print_speak(text, muted=MUTE)
    if MUTE:
        return None

    async def _speak_then_idle():
        from chotu.tools import local_speak
        await local_speak(text, face_pi=pi if _pi_reachable else None)
        _fire_face("idle")

    return asyncio.create_task(_speak_then_idle())


# --- Dispatch map ---

dispatch_map = build_dispatch(pi, estop)


# --- Goal mode state ---

goal_complete_event: asyncio.Event = asyncio.Event()
MAX_GOAL_ITERATIONS = int(os.getenv("CHOTU_GOAL_ITERATIONS", "40"))

from chotu.tools import set_goal_complete_event
set_goal_complete_event(goal_complete_event)


# --- Message building ---

def build_messages(user_input: str) -> list[dict]:
    sp = build_system_prompt(MODE)
    messages = [{"role": "system", "content": sp}]
    for entry in memory:
        messages.append(entry)
    messages.append({"role": "user", "content": user_input})
    return messages


# --- GUI event emitter ---

def _emit(event: dict) -> None:
    try:
        gui_event_queue.put_nowait(event)
    except asyncio.QueueFull:
        pass


def _extract_think_blocks(text: str | None) -> tuple[str | None, list[str]]:
    """Strip <think>...</think> blocks from text. Returns (clean_text, [think_texts])."""
    if not text:
        return text, []
    blocks = _THINK_RE.findall(text)
    clean = _THINK_RE.sub("", text).strip() or None
    return clean, blocks


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
    _emit({"type": "tool_call", "tool": name, "args": args, "ok": ok, "ms": ms,
           "error": result.get("error")})

def print_speak(text: str, muted: bool = False):
    label = "muted" if muted else "speaks"
    print(f'  [{label}] "{text}"')
    if not muted:
        _emit({"type": "speak", "text": text})

def print_monologue(text: str):
    if text and text.strip():
        print(f"  [thinks] {text.strip()}")
        _emit({"type": "monologue", "text": text.strip()})


# --- TTS helpers ---

def _fire_face(state: str) -> None:
    """Emit a face state event (stub — wired to Pi face API when available)."""
    _emit({"type": "face", "state": state})


def _fire_speak_if_content(content: str | None) -> "asyncio.Task | None":
    """Fire local_speak as a background task. Sets tts_done_event when all pending speaks finish."""
    global _pending_speaks
    if not content or not content.strip():
        return None
    text = content.strip()
    print_speak(text, muted=MUTE)
    if MUTE:
        if _pending_speaks == 0:
            tts_done_event.set()
        return None

    _pending_speaks += 1
    tts_done_event.clear()

    async def _speak_then_idle():
        global _pending_speaks
        from chotu.tools import local_speak
        await local_speak(text, face_pi=pi if _pi_reachable else None)
        _fire_face("idle")
        _pending_speaks -= 1
        if _pending_speaks == 0:
            tts_done_event.set()

    return asyncio.create_task(_speak_then_idle())


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

    _emit({"type": "user", "text": f"[goal] {goal_str}"})
    print(f"\n[goal] Starting: {goal_str}")
    goal_complete_event.clear()

    messages = [{"role": "system", "content": build_goal_prompt(goal_str)}]
    iterations = 0
    MAX_INNER = 12

    while iterations < MAX_GOAL_ITERATIONS:
        dbg(f"[goal] outer-loop top: event.is_set={goal_complete_event.is_set()}, iter={iterations}")
        if goal_complete_event.is_set():
            break

        state_str = await build_state_string()

        turn_label = "Begin pursuing your goal." if iterations == 0 else "Continue pursuing your goal."
        messages.append({"role": "user", "content": f"[state]\n{state_str}\n\n{turn_label}"})

        dbg(f"[goal] outer iteration {iterations + 1}, state: {state_str}")

        _fire_face("thinking")
        try:
            response = await llm_client.chat_complete(messages, GOAL_TOOL_SCHEMAS, thinking=thinking_enabled)
        except Exception as e:
            _fire_face("idle")
            print(f"  [goal] LLM error: {e}")
            break

        if not response.choices:
            print("  [goal] LLM returned empty choices")
            break

        # Strip think blocks, emit, fire speak
        if response.choices:
            content = response.choices[0].message.content
            clean_content, think_blocks = _extract_think_blocks(content)
            for block in think_blocks:
                block = block.strip()
                if block:
                    print(f"  [think] {block[:120]}...")
                    _emit({"type": "think", "text": block})
            if think_blocks and response.choices[0].message.content != clean_content:
                response.choices[0].message.content = clean_content
            if _fire_speak_if_content(clean_content):
                _goal_spoke = True

        set_legs_fired = 0
        waits_fired = 0
        goal_complete_fired = 0
        failed_tools: set[str] = set()
        deferred_vision: list[dict] = []
        inner_iterations = 0
        _goal_spoke = False

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
                elif name == "goal_complete" and goal_complete_fired >= 1:
                    suppressed.append(_suppressed(tc.id, name, "1 goal_complete per batch max"))
                elif name == "set_legs" and set_legs_fired >= 12:
                    suppressed.append(_suppressed(tc.id, name, "12 set_legs per turn max"))
                elif name == "wait" and waits_fired >= 1:
                    suppressed.append(_suppressed(tc.id, name, "1 wait per turn max"))
                elif name in failed_tools:
                    suppressed.append(_suppressed(tc.id, name, f"{name} already failed this turn"))
                else:
                    if name == "set_legs":      set_legs_fired += 1
                    if name == "wait":          waits_fired += 1
                    if name == "goal_complete": goal_complete_fired += 1
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
                        if len(gallery_store) >= 50:
                            gallery_store.pop(0)
                        gallery_store.append({"label": "capture", "image_b64": image_b64, "ts": time.time()})
                        _emit({"type": "image", "label": "capture", "image_b64": image_b64})
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
                response = await llm_client.chat_complete(messages, GOAL_TOOL_SCHEMAS, thinking=thinking_enabled)
            except Exception as e:
                print(f"  [goal] LLM follow-up error: {e}")
                break

            if not response.choices:
                break

            # Strip think blocks, emit, fire speak
            if response.choices:
                content = response.choices[0].message.content
                clean_content, think_blocks = _extract_think_blocks(content)
                for block in think_blocks:
                    block = block.strip()
                    if block:
                        print(f"  [think] {block[:120]}...")
                        _emit({"type": "think", "text": block})
                if think_blocks and response.choices[0].message.content != clean_content:
                    response.choices[0].message.content = clean_content
                if _fire_speak_if_content(clean_content):
                    _goal_spoke = True

            inner_iterations += 1

        if not _goal_spoke:
            _fire_face("idle")
        dbg(f"[goal] post-inner check: event.is_set={goal_complete_event.is_set()} iterations={iterations}")
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
                        from chotu.tools import local_speak
                        await local_speak(msg)
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
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        args = {}
    return tc, name, args_json, result


def _suppressed(tool_id: str, name: str, reason: str) -> tuple:
    """Fake ok envelope for a suppressed tool call — model sees success, doesn't retry."""
    result = {"ok": True, "tool": name, "result": {"suppressed": True}, "duration_ms": 0, "timestamp": time.time(), "error": None}
    dbg(f"[guard] suppressed {name}: {reason}")
    return tool_id, name, result


async def _process(user_input: str):
    _emit({"type": "user", "text": user_input})
    _fire_face("thinking")
    messages = build_messages(user_input)
    dbg(f"sending {len(messages)} messages to LLM")
    _spoke = False

    try:
        response = await llm_client.chat_complete(messages, TOOL_SCHEMAS, thinking=thinking_enabled)
    except Exception as e:
        print(f"  LLM error: {e}")
        _fire_face("idle")
        return

    if not response.choices:
        print("  LLM error: empty choices")
        _fire_face("idle")
        return

    # Strip think blocks, emit them, and fire speak from clean content
    if response.choices:
        content = response.choices[0].message.content
        clean_content, think_blocks = _extract_think_blocks(content)
        for block in think_blocks:
            block = block.strip()
            if block:
                print(f"  [think] {block[:120]}...")
                _emit({"type": "think", "text": block})
        if think_blocks and response.choices[0].message.content != clean_content:
            response.choices[0].message.content = clean_content
        if _fire_speak_if_content(clean_content):
            _spoke = True

    # Per-turn hard caps — enforced in code regardless of model behaviour
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
            if name == "set_legs" and set_legs_fired >= 12:
                suppressed.append(_suppressed(tc.id, name, "12 set_legs per turn max"))
            elif name == "wait" and waits_fired >= 1:
                suppressed.append(_suppressed(tc.id, name, "1 wait per turn max"))
            elif name in failed_tools:
                suppressed.append(_suppressed(tc.id, name, f"{name} already failed this turn"))
            else:
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

            if tool_call is None:
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
                    if len(gallery_store) >= 50:
                        gallery_store.pop(0)
                    gallery_store.append({"label": "capture", "image_b64": image_b64, "ts": time.time()})
                    _emit({"type": "image", "label": "capture", "image_b64": image_b64})
            else:
                messages.append(llm_client.format_tool_result(tool_call.id, json.dumps(result)))

        # Add suppressed tool results to message history (model must see a result for every call it made)
        for tool_id, name, result in suppressed:
            messages.append(llm_client.format_tool_result(tool_id, json.dumps(result)))

        for msg in deferred_vision:
            messages.append(msg)

        dbg(f"follow-up LLM call (iteration {iterations + 1})")
        try:
            response = await llm_client.chat_complete(messages, TOOL_SCHEMAS, thinking=thinking_enabled)
        except Exception as e:
            print(f"  LLM error on follow-up: {e}")
            return

        if not response.choices:
            print("  LLM error: empty choices on follow-up")
            return

        # Strip think blocks, emit, and fire speak from clean content
        if response.choices:
            content = response.choices[0].message.content
            clean_content, think_blocks = _extract_think_blocks(content)
            for block in think_blocks:
                block = block.strip()
                if block:
                    print(f"  [think] {block[:120]}...")
                    _emit({"type": "think", "text": block})
            if think_blocks and response.choices[0].message.content != clean_content:
                response.choices[0].message.content = clean_content
            if _fire_speak_if_content(clean_content):
                _spoke = True

        iterations += 1

    if iterations >= MAX_TOOL_ITERATIONS:
        print("  [safety] Tool call limit reached, stopping.")

    final_text = response.choices[0].message.content
    if not _spoke:
        _fire_face("idle")

    memory.append({"role": "user", "content": user_input})
    if final_text:
        memory.append({"role": "assistant", "content": final_text})


# --- Goal runner ---

async def goal_runner_task(initial_goal: str) -> None:
    """Run goals sequentially. After each completes, sit and wait for next goal via terminal."""
    current_goal = initial_goal

    while True:
        await run_goal(current_goal)

        print("\n[goal] Sitting down. Type next goal or Ctrl+C to quit.")
        try:
            await pi.pose("sit")
        except Exception:
            pass

        try:
            next_goal = await asyncio.to_thread(input, "next goal> ")
            if next_goal.strip():
                current_goal = next_goal.strip()
            else:
                print("[goal] No goal entered. Waiting for input...")
                continue
        except EOFError:
            break


async def set_mode(mode: str, goal_text: str | None = None) -> None:
    global _active_goal_task
    if mode == "goal" and goal_text:
        if _active_goal_task and not _active_goal_task.done():
            _active_goal_task.cancel()
        _active_goal_task = asyncio.create_task(goal_runner_task(goal_text))
        _emit({"type": "mode", "mode": "goal"})
    elif mode == "reactive":
        if _active_goal_task and not _active_goal_task.done():
            _active_goal_task.cancel()
            _active_goal_task = None
        _emit({"type": "mode", "mode": "reactive"})


# --- Input loops ---

async def input_loop():
    while True:
        try:
            text = await asyncio.to_thread(input, "you> ")
            input_queue.put_nowait(text)
        except EOFError:
            break


async def voice_loop():
    global continuous_mode
    import time as _time
    from chotu.voice import VoiceListener, CONTINUOUS_SILENCE_TIMEOUT
    listener = VoiceListener()
    listener.start()
    last_speech_time = _time.monotonic()
    print("  [voice] Voice input active — say 'Hey Jarvis' to speak to Chotu.")

    while True:
        try:
            if not continuous_mode:
                await asyncio.to_thread(listener.wait_wake_word)
                listener.drain()
            else:
                await tts_done_event.wait()
                tts_done_event.clear()
                listener.drain()

                if _time.monotonic() - last_speech_time > CONTINUOUS_SILENCE_TIMEOUT:
                    continuous_mode = False
                    print("  [voice] Silence timeout — dropping to wake-word mode.")
                    continue

            text = await asyncio.to_thread(listener.record_utterance)

            if text.strip():
                last_speech_time = _time.monotonic()
                input_queue.put_nowait(text)

        except Exception as e:
            print(f"  [voice error] {e}")
            await asyncio.sleep(1.0)


# --- Main ---

async def main(goal: str | None = None):
    mode_label = "autonomous" if goal else MODE
    print(f"Chotu brain started (mode: {mode_label}, model: {llm_client.model}, provider: {llm_client.provider})")
    if MUTE:
        print("  [mute] Audio disabled — speak() calls logged but not sent to Pi.")
    print(f"Pi bridge: {PI_HOST}")

    global _pi_reachable
    health = await pi.health()
    if health.get("ok"):
        print("Pi bridge: connected")
        _pi_reachable = True
    else:
        print(f"Pi bridge: NOT reachable ({health.get('error', '?')})")
        print("  Tools will return error envelopes. Continuing anyway.")

    import sys as _sys
    _sys.modules.setdefault('chotu.brain', _sys.modules['__main__'])
    from chotu import gui_server
    loop = asyncio.get_running_loop()
    _shutdown = asyncio.Event()

    def _on_signal():
        if not _shutdown.is_set():
            print("\n[shutdown] Ctrl+C — stopping...")
            _shutdown.set()

    loop.add_signal_handler(signal.SIGINT, _on_signal)
    loop.add_signal_handler(signal.SIGTERM, _on_signal)

    tasks = [
        asyncio.create_task(obstacle_poller(pi, estop)),
        asyncio.create_task(battery_monitor()),
        asyncio.create_task(gui_server.run_gui_server()),
    ]

    print("Type a message to talk to Chotu. Ctrl+C to quit.\n")
    tasks.append(asyncio.create_task(brain_loop()))
    tasks.append(asyncio.create_task(voice_loop() if VOICE_ENABLED else input_loop()))
    if goal:
        print(f"Goal: {goal}\n")
        tasks.append(asyncio.create_task(goal_runner_task(goal)))

    _stop_task = asyncio.create_task(_shutdown.wait())

    try:
        done, _ = await asyncio.wait(tasks + [_stop_task], return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t is _stop_task or t.cancelled():
                continue
            exc = t.exception()
            if exc:
                print(f"\n[fatal] Unhandled exception: {exc}")
                traceback.print_exc()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for t in tasks + [_stop_task]:
            t.cancel()
        await asyncio.gather(*tasks, _stop_task, return_exceptions=True)
        await llm_client.close()
        print("\nChotu sitting down...")
        try:
            await asyncio.wait_for(pi.pose("sit"), timeout=5.0)
        except Exception:
            pass
        await pi.close()
        print("Chotu shutting down. Bye!")
        import os as _os
        _os._exit(0)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Chotu brain")
    parser.add_argument("--goal", type=str, default=None, help="Goal for autonomous mode")
    args = parser.parse_args()
    asyncio.run(main(goal=args.goal))
