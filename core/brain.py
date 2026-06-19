"""Chotu's brain — single infinite paced loop, memory buffer, terminal input.

Loads PALIV.md (framework contract) + CHOTU_BASE.md (persona + heartbeat
rhythm) into the system prompt at import time, via core.prompts.
"""

import asyncio
import json
import os
import re
import signal
import time
import traceback
from dotenv import load_dotenv

# Pre-launch config screen — must run BEFORE the env-reading core.* imports below
# so the user's picks win over .env (load_dotenv uses override=False). No-op for
# non-TTY / PALIV_NO_LAUNCHER=1 / when imported as a module (chotu skill, dry_run).
if __name__ == "__main__":
    from core.launcher import run_launcher
    run_launcher()

from core.llm_client import LLMClient
from core.pi_client import PiClient
from core.prompts import SYSTEM_PROMPT
from core.tools import TOOL_SCHEMAS, build_dispatch, dispatch_tool
from core.loop_helpers import (
    describe_motion, motion_from_calls, push_frame, render_frames,
    maybe_compact, cap_result, pace_remainder, split_tool_calls,
    PendingInput, paced_sleep,
)
from core.scratchpad import Scratchpad
from core.session_profiler import SessionProfiler


# --- Config ---

load_dotenv()

PI_HOST = os.getenv("PI_HOST", "http://chotu.local:7000")
DEBUG = os.getenv("PALIV_DEBUG", "0") == "1"
MUTE = os.getenv("PALIV_MUTE", "0") == "1"
TICK_INTERVAL = int(os.getenv("PALIV_TICK_INTERVAL", "5"))
VOICE_ENABLED = os.getenv("PALIV_VOICE", "0") == "1"
LOOP_FLOOR = float(os.getenv("PALIV_LOOP_FLOOR", "3"))   # min seconds between calls (was 2)
PTT_ENABLED = os.getenv("PALIV_PTT", "0") == "1"   # GUI push-to-talk + hands-free button
COMPACT_AT_TOKENS   = int(os.getenv("PALIV_COMPACT_AT_TOKENS", "10000"))   # est. memory tokens that trigger a trim
COMPACT_KEEP_TOKENS = int(os.getenv("PALIV_COMPACT_KEEP_TOKENS", "6000"))  # est. memory tokens retained after a trim


listen_and_transcribe = None
if VOICE_ENABLED:
    from core.voice import listen_and_transcribe


def strip_internal_fields(messages: list[dict]) -> list[dict]:
    """Return copy with _origin (and any _ prefix fields) removed — safe to send to LLM."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


# --- Globals ---

pi = PiClient(PI_HOST)
llm_client = LLMClient()
memory: list[dict] = []  # append-only window; batch-trimmed by maybe_compact()
frame_stack: list[dict] = []           # newest-last, capped at 3 by push_frame
scratchpad: Scratchpad = Scratchpad()  # mechanical running state, rendered each turn
pending_input: PendingInput = PendingInput()
BOOT_TEXT = "[boot] You just woke up. You don't know where you are. The session starts here."
OBSTACLE_CM = 15
estop: asyncio.Event = asyncio.Event()
gui_event_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
gallery_store: list[dict] = []
thinking_enabled: bool = False
_pi_reachable: bool = False
_ptt_capturing: bool = False           # single-flight guard for one-shot push-to-talk
handsfree_task: "asyncio.Task | None" = None  # running hands-free loop, or None
_usage = {"calls": 0, "prompt": 0, "completion": 0, "cached": 0, "t0": None}  # cumulative token meter
_profiler = SessionProfiler()
_last_battery: dict = {}  # {"percent": N, "voltage": N} — updated by battery_monitor

continuous_mode: bool = False
tts_done_event: asyncio.Event = asyncio.Event()
tts_done_event.set()  # initially ready — no TTS playing at startup

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


# --- Dispatch map ---

# motion_lock: enforces single-motion-at-a-time across move/pose.
# Lives at module scope so events.py can observe its state.
from core.motion_lock import MotionLock

motion_lock = MotionLock()

dispatch_map = build_dispatch(
    pi, estop, mute=MUTE, motion_lock=motion_lock,
)


# --- Message building ---

def build_loop_messages(system_prompt: str, memory: list[dict], frame_stack: list[dict],
                        scratchpad: "Scratchpad", cache_boundary: bool = False) -> list[dict]:
    """System prompt + append-only window + state block + the 3 motion-labeled frames.
    Order is [system | memory | STATE | frames]: system+memory are the stable cached
    prefix; STATE and frames are the small volatile tail. Internal `_origin` fields are
    stripped so the result is safe to send to the LLM. When `cache_boundary` is set, the
    last memory message is tagged `_cache_boundary` for the provider to mark cache_control."""
    msgs = [{"role": "system", "content": system_prompt}]
    mem_msgs = strip_internal_fields(memory)
    if cache_boundary and mem_msgs:
        mem_msgs[-1] = {**mem_msgs[-1], "_cache_boundary": True}
    msgs.extend(mem_msgs)
    state = scratchpad.render()
    if state is not None:
        msgs.append({k: v for k, v in state.items() if not k.startswith("_")})
    msgs.extend(render_frames(frame_stack))
    return msgs


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
    """Emit a face-state event to the GUI and update the Pi's physical face panel."""
    _emit({"type": "face", "state": state})
    if _pi_reachable:
        asyncio.create_task(pi.set_face(name=state))


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


# --- Battery monitor ---

BATTERY_POLL_INTERVAL = 2  # seconds — fast enough to catch voltage sag before brownout
BATTERY_MIN_VALID_VOLTAGE = 5.5  # below this, the ADC is reading a brownout transient — discard
BATTERY_CONSECUTIVE_REQUIRED = 3  # threshold must hold this many polls in a row before firing
_BATTERY_THRESHOLDS = [
    (15, "battery critical. fifteen percent. plug in now friend."),
    (50, "battery fifty percent. halfway gone."),
]

async def battery_monitor() -> None:
    """Poll battery every BATTERY_POLL_INTERVAL seconds. Emit voltage to GUI on every poll;
    speak once when a threshold holds for BATTERY_CONSECUTIVE_REQUIRED polls in a row.
    Readings below BATTERY_MIN_VALID_VOLTAGE are treated as brownout transients and ignored."""
    await asyncio.sleep(10.0)  # startup delay
    fired: set[int] = set()
    streak: dict[int, int] = {t: 0 for t, _ in _BATTERY_THRESHOLDS}
    while True:
        result = await pi.get_battery()
        if result.get("ok"):
            pct = result.get("result", {}).get("percent", 100)
            voltage = result.get("result", {}).get("voltage", 0)
            if voltage < BATTERY_MIN_VALID_VOLTAGE:
                # bogus brownout-transient read; do not update state or fire warnings
                await asyncio.sleep(BATTERY_POLL_INTERVAL)
                continue
            _last_battery["percent"] = pct
            _last_battery["voltage"] = voltage
            _emit({"type": "battery", "percent": pct, "voltage": voltage})
            for threshold, msg in _BATTERY_THRESHOLDS:
                if pct <= threshold:
                    streak[threshold] += 1
                else:
                    streak[threshold] = 0
                if streak[threshold] >= BATTERY_CONSECUTIVE_REQUIRED and threshold not in fired:
                    fired.add(threshold)
                    print(f"[battery] {pct:.0f}% ({voltage:.2f}V) — warning at {threshold}%")
                    pending_input.push(f"[battery] {msg}")
        await asyncio.sleep(BATTERY_POLL_INTERVAL)


# --- Paced loop ---

async def paced_loop():
    """The brain. Runs forever: one iteration, then a paced sleep (>= LOOP_FLOOR,
    cut short by incoming input)."""
    memory.append({"role": "user", "content": BOOT_TEXT, "_origin": "boot"})
    while True:
        try:
            tool_duration = await run_iteration()
        except Exception as e:
            print(f"  [brain error] {e}")
            traceback.print_exc()
            tool_duration = 0.0
        await paced_sleep(pace_remainder(tool_duration, LOOP_FLOOR), pending_input)


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


def _safe_args(args_json: str) -> dict:
    try:
        return json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        return {"_raw": args_json}


async def run_iteration() -> float:
    """One loop turn: drain input -> LLM call -> dispatch deduped tools -> capture a
    fresh labeled frame -> trim context. Returns tool_duration seconds (for pacing)."""
    text = pending_input.drain()
    if text:
        memory.append({"role": "user", "content": f"[human] {text}", "_origin": "user"})
        _emit({"type": "user", "text": text})

    _fire_face("thinking")
    messages = build_loop_messages(
        SYSTEM_PROMPT, memory, frame_stack, scratchpad,
        cache_boundary=llm_client.supports_cache_control,
    )

    try:
        response = await llm_client.chat_complete(messages, TOOL_SCHEMAS, thinking=thinking_enabled)
    except Exception as e:
        print(f"  LLM error: {e}")
        _fire_face("idle")
        return 0.0
    if not response.choices:
        print("  LLM error: empty choices")
        _fire_face("idle")
        return 0.0

    if response.usage:
        if _usage["t0"] is None:
            _usage["t0"] = time.time()
        _usage["calls"] += 1
        p = response.usage.get("prompt_tokens", 0)
        cached = response.usage.get("cached_tokens", 0)
        _usage["prompt"] += p
        _usage["completion"] += response.usage.get("completion_tokens", 0)
        _usage["cached"] += cached
        total = _usage["prompt"] + _usage["completion"]
        elapsed = time.time() - _usage["t0"]
        rate = f"~{total/(elapsed/60.0)/1000:.1f}k/min" if elapsed > 1.0 else "~—"
        # Effective input @ explicit-cache pricing: cache hits bill at 10%.
        eff_prompt = _usage["prompt"] - _usage["cached"] * 0.9
        hit = f" cache {cached}/{p}" if cached else ""
        print(
            f"  [tokens] turn={p}p/"
            f"{response.usage.get('completion_tokens', 0)}c{hit}  "
            f"cum={total/1000:.1f}k ({_usage['prompt']/1000:.1f}k prompt + "
            f"{_usage['completion']/1000:.1f}k compl) over {_usage['calls']} calls  "
            f"eff_in~{eff_prompt/1000:.1f}k  {rate}"
        )

    msg = response.choices[0].message
    clean_content, think_blocks = _extract_think_blocks(msg.content)
    for block in think_blocks:
        if block.strip():
            print(f"  [think] {block.strip()[:120]}...")
            _emit({"type": "think", "text": block.strip()})
    if clean_content:
        print_monologue(clean_content)

    assistant_msg = llm_client.format_assistant_message(response)
    assistant_msg["content"] = clean_content or ""
    assistant_msg["_origin"] = "loop"
    memory.append(assistant_msg)

    tool_duration = 0.0
    motion_desc = "no movement"
    if msg.tool_calls:
        keep, suppressed = split_tool_calls(msg.tool_calls)
        start = time.time()
        dispatched = await asyncio.gather(
            *[_run_one(tc) for tc in keep]
        )
        tool_duration = time.time() - start
        motion_calls = []
        state_calls = []
        for tc, name, args_json, result in dispatched:
            args = _safe_args(args_json)
            print_tool_call(name, args, result)
            memory.append(llm_client.format_tool_result(tc.id, cap_result(json.dumps(result))))
            motion_calls.append((name, args))
            state_calls.append((name, args, result))
        for tc in suppressed:
            env = {"ok": True, "tool": tc.function.name, "result": {"suppressed": True},
                   "duration_ms": 0, "timestamp": time.time(), "error": None}
            memory.append(llm_client.format_tool_result(tc.id, cap_result(json.dumps(env))))
        motion_desc = motion_from_calls(motion_calls)
        scratchpad.update(state_calls)

    capture = await pi.capture()
    if capture.get("ok"):
        frame_b64 = capture.get("result", {}).get("image_base64", "")
        if frame_b64:
            push_frame(frame_stack, frame_b64, motion_desc)
            _emit({"type": "image", "label": "frame", "image_b64": frame_b64})

    maybe_compact(memory, COMPACT_AT_TOKENS, COMPACT_KEEP_TOKENS)
    _fire_face("idle")
    return tool_duration


# --- Push-to-talk (GUI) ---

async def trigger_ptt_capture() -> None:
    """One-shot push-to-talk. No-op if a capture is already running or hands-free is on.
    Records one utterance (VAD stop) and pushes the transcript to pending_input."""
    global _ptt_capturing
    if _ptt_capturing or handsfree_task is not None:
        return
    _ptt_capturing = True
    _emit({"type": "ptt", "state": "recording"})
    try:
        from core.voice import record_push_to_talk
        text = await record_push_to_talk()
        if text.strip():
            pending_input.push(text)
    except Exception as e:
        print(f"  [ptt error] {e}")
    finally:
        _ptt_capturing = False
        _emit({"type": "ptt", "state": "idle"})


def set_handsfree(enabled: bool) -> None:
    """Start or stop the hands-free conversation loop (idempotent)."""
    global handsfree_task
    if enabled and handsfree_task is None:
        handsfree_task = asyncio.create_task(_handsfree_loop())
    elif not enabled and handsfree_task is not None:
        handsfree_task.cancel()
        handsfree_task = None


async def _handsfree_loop() -> None:
    """Latched hands-free: record an utterance, push it, wait for Chotu to finish
    speaking, repeat. No silence timeout — runs until set_handsfree(False) cancels it."""
    from core.voice import VoiceListener
    listener = VoiceListener()
    listener.start()
    _emit({"type": "ptt", "state": "handsfree_on"})
    first = True
    try:
        while True:
            if not first:
                await tts_done_event.wait()   # let Chotu finish before listening again
                tts_done_event.clear()
            first = False
            listener.drain()
            _emit({"type": "ptt", "state": "handsfree_listening"})
            text = await asyncio.to_thread(listener.record_utterance)
            _emit({"type": "ptt", "state": "handsfree_on"})
            if text.strip():
                pending_input.push(text)
    except asyncio.CancelledError:
        pass
    finally:
        listener.stop()
        _emit({"type": "ptt", "state": "handsfree_off"})


# --- Input loops ---

async def input_loop():
    while True:
        try:
            text = await asyncio.to_thread(input, "you> ")
            pending_input.push(text)
        except EOFError:
            break


async def voice_loop():
    global continuous_mode
    import time as _time
    from core.voice import VoiceListener, CONTINUOUS_SILENCE_TIMEOUT
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
                pending_input.push(text)

        except Exception as e:
            print(f"  [voice error] {e}")
            await asyncio.sleep(1.0)


# --- Main ---

async def main():
    print(f"Chotu brain started (model: {llm_client.model}, provider: {llm_client.provider})")
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

    _profiler.attach(llm_client)

    import sys as _sys
    _sys.modules.setdefault('core.brain', _sys.modules['__main__'])
    from core import gui_server
    loop = asyncio.get_running_loop()
    from core.tools import register_speak_done_event
    register_speak_done_event(tts_done_event)
    _shutdown = asyncio.Event()

    def _on_signal():
        if not _shutdown.is_set():
            print("\n[shutdown] Ctrl+C — stopping...")
            _shutdown.set()

    loop.add_signal_handler(signal.SIGINT, _on_signal)
    loop.add_signal_handler(signal.SIGTERM, _on_signal)

    def _on_stop_word():
        pending_input.push("[stop] freeze — a human asked you to stop.")

    try:
        loop.add_signal_handler(signal.SIGUSR1, _on_stop_word)
    except (NotImplementedError, RuntimeError):
        pass  # not all platforms support SIGUSR1

    tasks = [
        asyncio.create_task(obstacle_poller(pi, estop)),
        asyncio.create_task(battery_monitor()),
        asyncio.create_task(gui_server.run_gui_server()),
    ]

    print("Type a message to talk to Chotu. Ctrl+C to quit.\n")
    tasks.append(asyncio.create_task(paced_loop()))
    tasks.append(asyncio.create_task(voice_loop() if VOICE_ENABLED else input_loop()))

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
        from pathlib import Path as _Path
        _out = _profiler.save(
            _Path(__file__).resolve().parent.parent / "out",
            llm_client.model, _pi_reachable,
        )
        if _out:
            print(f"Session profile saved → {_out}")
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
    asyncio.run(main())
