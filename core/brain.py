"""Chotu's brain — live loop, memory buffer, terminal input.

Loads PALIV.md (framework contract) + CHOTU_BASE.md (persona) + a
mode-specific overlay (CHOTU_STATELESS.md or CHOTU_LIVE.md per
PALIV_BRAIN_MODE) into the system prompt at import time.
"""

import asyncio
import itertools
import json
import os
import re
import signal
import time
import traceback
from dotenv import load_dotenv

from core.heartbeat import heartbeat_loop
from core.llm_client import LLMClient
from core.pi_client import PiClient
from core.prompts import SYSTEM_PROMPT
from core.tools import TOOL_SCHEMAS, build_dispatch, dispatch_tool, capture_vision_tool
from core import explore_agent


# --- Config ---

load_dotenv()

PI_HOST = os.getenv("PI_HOST", "http://chotu.local:7000")
MEMORY_TOKEN_BUDGET = int(os.getenv("PALIV_MEMORY_TOKENS", "12000"))
MAX_TOOL_ITERATIONS = 6
DEBUG = os.getenv("PALIV_DEBUG", "0") == "1"
MUTE = os.getenv("PALIV_MUTE", "0") == "1"
TICK_INTERVAL = int(os.getenv("PALIV_TICK_INTERVAL", "5"))
VOICE_ENABLED = os.getenv("PALIV_VOICE", "0") == "1"
HEARTBEAT_WINDOW = int(os.getenv("PALIV_HEARTBEAT_WINDOW", "5"))


listen_and_transcribe = None
if VOICE_ENABLED:
    from core.voice import listen_and_transcribe


# --- Priority input queue ---

_priority_counter = itertools.count()

class _PriorityQueue:
    """Queue that gives user/event/boot items priority over heartbeats."""

    def __init__(self):
        self._q: asyncio.PriorityQueue = asyncio.PriorityQueue()

    def put_nowait(self, item: dict):
        kind = item.get("kind", "heartbeat")
        priority = 0 if kind in ("user", "event", "boot") else 1
        self._q.put_nowait((priority, next(_priority_counter), item))

    async def get(self) -> dict:
        _, _, item = await self._q.get()
        return item

    def empty(self) -> bool:
        return self._q.empty()

    def qsize(self) -> int:
        return self._q.qsize()


# --- Tagged input items ---

def wrap_user_input(text: str) -> dict:
    return {"kind": "user", "text": text}

def wrap_heartbeat() -> dict:
    return {"kind": "heartbeat", "text": "[heartbeat]"}

def wrap_event(subkind: str, payload: str = "") -> dict:
    body = f"[event] {subkind}" + (f": {payload}" if payload else "")
    return {"kind": "event", "subkind": subkind, "text": body}

def wrap_boot() -> dict:
    return {"kind": "boot", "text": "[boot] You just woke up. You don't know where you are. The session starts here."}


# --- Memory helpers ---

def _estimate_tokens(messages: list[dict]) -> int:
    """Rough char/4 token estimate. Cheap upper bound that's fine for budget enforcement."""
    n = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            n += len(content) // 4
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    txt = part.get("text") or ""
                    n += len(txt) // 4
        for tc in m.get("tool_calls", []) or []:
            args = (tc.get("function") or {}).get("arguments", "")
            n += len(args) // 4
        if m.get("tool_call_id"):
            n += 4  # small bookkeeping
    return n


def trim_memory(items: list[dict], max_tokens: int = None) -> list[dict]:
    """Drop oldest items until under budget. Tool call/result pairs are dropped as units.

    A "pair" is an assistant message with `tool_calls` plus all subsequent `role=tool` messages
    whose `tool_call_id` matches one of those calls. Pairs are scanned from the front; the whole
    pair is dropped or kept.
    """
    budget = max_tokens if max_tokens is not None else MEMORY_TOKEN_BUDGET
    if _estimate_tokens(items) <= budget:
        return list(items)

    work = list(items)
    while _estimate_tokens(work) > budget and work:
        head = work[0]
        if head.get("role") == "assistant" and head.get("tool_calls"):
            ids = {tc["id"] for tc in head["tool_calls"]}
            # drop head + any immediately-following tool results whose id matches
            i = 1
            while i < len(work) and work[i].get("role") == "tool" and work[i].get("tool_call_id") in ids:
                i += 1
            del work[:i]
        else:
            del work[0]
    return work


def evict_old_heartbeats(messages: list[dict]) -> None:
    """Trim heartbeat blocks so at most HEARTBEAT_WINDOW user[heartbeat] markers remain. Mutates in place."""
    hb_starts = [i for i, m in enumerate(messages)
                 if m.get("_origin") == "heartbeat" and m.get("role") == "user"]
    if len(hb_starts) <= HEARTBEAT_WINDOW:
        return
    to_evict = hb_starts[: len(hb_starts) - HEARTBEAT_WINDOW]
    boundaries = []
    for k, start in enumerate(to_evict):
        end = hb_starts[k + 1] if k + 1 < len(hb_starts) else len(messages)
        boundaries.append((start, end))
    for start, end in reversed(boundaries):
        del messages[start:end]


def strip_internal_fields(messages: list[dict]) -> list[dict]:
    """Return copy with _origin (and any _ prefix fields) removed — safe to send to LLM."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


# --- Explore subagent dispatch ---

async def dispatch_explore_tool(pi, args: dict) -> dict:
    import time as _t
    started = _t.time()
    reason = args.get("reason", "idle")
    envelope = await explore_agent.run_explore(pi, reason=reason)
    ok = envelope["status"] in ("done", "cap_nodes")
    return {
        "ok": ok,
        "tool": "explore",
        "result": envelope,
        "duration_ms": int((_t.time() - started) * 1000),
        "timestamp": _t.time(),
        "error": None if ok else envelope.get("message"),
    }


# --- Globals ---

pi = PiClient(PI_HOST)
llm_client = LLMClient()
memory: list[dict] = []  # rolling window; trimmed by trim_memory()
input_queue: _PriorityQueue = _PriorityQueue()
user_input_pending: asyncio.Event = asyncio.Event()
tool_chain_active: asyncio.Event = asyncio.Event()
OBSTACLE_CM = 15
estop: asyncio.Event = asyncio.Event()
gui_event_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
gallery_store: list[dict] = []
thinking_enabled: bool = False
_pi_reachable: bool = False
_last_battery: dict = {}  # {"percent": N, "voltage": N} — updated by battery_monitor

continuous_mode: bool = False
tts_done_event: asyncio.Event = asyncio.Event()
tts_done_event.set()  # initially ready — no TTS playing at startup

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


# --- Dispatch map ---

# motion_lock: enforces single-motion-at-a-time across move/pose/set_legs/do_trick.
# Lives at module scope so events.py and the future live-mode FrameSampler can
# observe its state. frame_sampler is wired in main() once the backend is up.
from core.motion_lock import MotionLock

motion_lock = MotionLock()
_frame_sampler_ref: dict = {"sampler": None}

dispatch_map = build_dispatch(
    pi, estop, mute=MUTE, motion_lock=motion_lock, frame_sampler=None,
)
dispatch_map["explore"] = lambda **kw: dispatch_explore_tool(pi, kw)


# --- Message building ---

def build_messages(user_input: str, *, origin: str = "user") -> list[dict]:
    global memory
    memory = trim_memory(memory)
    messages = [{"role": "system", "content": SYSTEM_PROMPT, "_origin": "boot"}]
    messages.extend(memory)
    messages.append({"role": "user", "content": user_input, "_origin": origin})
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
                    from core.events import inject_event
                    inject_event(input_queue, tool_chain_active, "battery_low",
                                 payload=f"{pct:.0f}% ({voltage:.2f}V) — {msg}")
        await asyncio.sleep(BATTERY_POLL_INTERVAL)


# --- Live loop ---

async def live_loop():
    while True:
        item = await input_queue.get()
        if isinstance(item, str):
            item = wrap_user_input(item)  # backwards-compat for any legacy str pushes
        text = item.get("text", "").strip()
        if not text:
            continue
        if item.get("kind") in ("user", "event"):
            user_input_pending.clear()
        print(f"\n--- Chotu thinking ({item['kind']}) ---")
        tool_chain_active.set()
        try:
            await _process(item)
        except Exception as e:
            print(f"  [brain error] {e}")
            traceback.print_exc()
        finally:
            tool_chain_active.clear()
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


def _guard_key(name: str, args: dict) -> tuple:
    """Stable hash key for per-args fail guard."""
    import hashlib
    blob = json.dumps(args or {}, sort_keys=True, default=str)
    h = hashlib.sha1(blob.encode()).hexdigest()[:16]
    return (name, h)


def _record_failure(failed_set: set, name: str, args: dict) -> None:
    failed_set.add(_guard_key(name, args))


def _should_suppress(failed_set: set, name: str, args: dict) -> bool:
    return _guard_key(name, args) in failed_set


def _suppressed(tool_id: str, name: str, reason: str) -> tuple:
    """Fake ok envelope for a suppressed tool call — model sees success, doesn't retry."""
    result = {"ok": True, "tool": name, "result": {"suppressed": True}, "duration_ms": 0, "timestamp": time.time(), "error": None}
    dbg(f"[guard] suppressed {name}: {reason}")
    return tool_id, name, result


async def _process(item: dict):
    user_input = item["text"]
    kind = item["kind"]
    _emit({"type": kind, "text": user_input})
    _fire_face("thinking")
    messages = build_messages(user_input, origin=kind)
    if kind == "heartbeat":
        evict_old_heartbeats(messages)
    dbg(f"sending {len(messages)} messages to LLM")

    try:
        response = await llm_client.chat_complete(strip_internal_fields(messages), TOOL_SCHEMAS, thinking=thinking_enabled)
    except Exception as e:
        print(f"  LLM error: {e}")
        _fire_face("idle")
        return

    if not response.choices:
        print("  LLM error: empty choices")
        _fire_face("idle")
        return

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
        if clean_content:
            print_monologue(clean_content)

    set_legs_fired = 0
    waits_fired = 0
    failed_tools: set = set()

    iterations = 0
    ITERATION_CAP = MAX_TOOL_ITERATIONS

    while response.choices[0].message.tool_calls and iterations < ITERATION_CAP:
        assistant_msg = response.choices[0].message
        messages.append(llm_client.format_assistant_message(response))

        # --- Split: allowed vs suppressed ---
        to_dispatch = []
        suppressed = []
        for tc in assistant_msg.tool_calls:
            name = tc.function.name
            try:
                tc_args = json.loads(tc.function.arguments or "{}") if tc.function.arguments else {}
            except json.JSONDecodeError:
                tc_args = {}
            if name == "set_legs" and set_legs_fired >= 12:
                suppressed.append(_suppressed(tc.id, name, "12 set_legs per turn max"))
            elif name == "wait" and waits_fired >= 1:
                suppressed.append(_suppressed(tc.id, name, "1 wait per turn max"))
            elif _should_suppress(failed_tools, name, tc_args):
                suppressed.append(_suppressed(tc.id, name, f"{name} already failed this turn"))
            else:
                if name == "set_legs": set_legs_fired += 1
                if name == "wait":    waits_fired += 1
                to_dispatch.append(tc)

        deferred_vision = []

        async def _run_one_scoped(tc, dmap):
            from core.tools import dispatch_tool
            name = tc.function.name
            args_json = tc.function.arguments
            dbg(f"dispatching {name}({args_json})")
            if name not in dmap:
                env = {"ok": False, "tool": name, "result": {}, "duration_ms": 0,
                       "timestamp": time.time(),
                       "error": f"'{name}' is not available in this context"}
                return tc, name, args_json, env
            result = await dispatch_tool(dmap, name, args_json)
            return tc, name, args_json, result

        dispatched = await asyncio.gather(*[_run_one_scoped(tc, dispatch_map) for tc in to_dispatch])

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
                _record_failure(failed_tools, name, args)

            if tool_call is None:
                continue

            # --- normal tool result handling ---
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

        for tool_id, name, result in suppressed:
            messages.append(llm_client.format_tool_result(tool_id, json.dumps(result)))

        for msg in deferred_vision:
            messages.append(msg)

        dbg(f"follow-up LLM call (iteration {iterations + 1})")
        try:
            response = await llm_client.chat_complete(strip_internal_fields(messages), TOOL_SCHEMAS, thinking=thinking_enabled)
        except Exception as e:
            print(f"  LLM error on follow-up: {e}")
            return

        if not response.choices:
            print("  LLM error: empty choices on follow-up")
            return

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
            if clean_content:
                print_monologue(clean_content)

        iterations += 1

    if iterations >= ITERATION_CAP:
        print("  [safety] Tool call limit reached, stopping.")

    final_text = response.choices[0].message.content
    _fire_face("idle")

    if kind == "heartbeat" and iterations == 0:
        return

    memory.append({"role": "user", "content": user_input, "_origin": kind})
    memory.append({"role": "assistant", "content": final_text or "", "_origin": kind})



# --- Input loops ---

async def input_loop():
    while True:
        try:
            text = await asyncio.to_thread(input, "you> ")
            input_queue.put_nowait(wrap_user_input(text))
            user_input_pending.set()
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
                from core.events import inject_event
                if not inject_event(input_queue, tool_chain_active, "wake_word", payload=text):
                    print(f"  [voice] dropped wake_word during tool chain: {text!r}")
                else:
                    user_input_pending.set()

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
        from core.events import inject_event
        inject_event(input_queue, tool_chain_active, "stop_word")

    try:
        loop.add_signal_handler(signal.SIGUSR1, _on_stop_word)
    except (NotImplementedError, RuntimeError):
        pass  # not all platforms support SIGUSR1

    tasks = [
        asyncio.create_task(obstacle_poller(pi, estop)),
        asyncio.create_task(battery_monitor()),
        asyncio.create_task(gui_server.run_gui_server()),
        asyncio.create_task(heartbeat_loop(input_queue, tool_chain_active)),
    ]

    print("Type a message to talk to Chotu. Ctrl+C to quit.\n")
    tasks.append(asyncio.create_task(live_loop()))

    # Prime the monologue with one synthetic [boot] message.
    input_queue.put_nowait(wrap_boot())

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
