"""OpenAI tool schemas and dispatch map for Chotu."""

import asyncio
import json
import os
import time

from dotenv import load_dotenv
load_dotenv()

_tts_lock: asyncio.Lock | None = None

def _get_tts_lock() -> asyncio.Lock:
    global _tts_lock
    if _tts_lock is None:
        _tts_lock = asyncio.Lock()
    return _tts_lock

from core.pi_client import PiClient
from core.motion_lock import MotionLock

_ALL_SPELLS = ["lumos", "nox", "avada_kedavra"]
_raw = os.getenv("SPELLS_ENABLED", "lumos,nox,avada_kedavra")
_ENABLED_SPELLS = [s.strip() for s in _raw.split(",") if s.strip() in _ALL_SPELLS] or _ALL_SPELLS


# --- OpenAI function-calling tool schemas ---

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "move",
            "description": (
                "Walk in a direction. 1 step is about 45mm (1.8 inches). "
                "1 turn is about 30 degrees. speed 0-100, default 70."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["forward", "backward", "turn left", "turn right"],
                        "description": "Direction to move.",
                    },
                    "steps": {
                        "type": "integer",
                        "description": "Number of steps to take. Default 1.",
                        "default": 1,
                    },
                    "speed": {
                        "type": "integer",
                        "description": "Servo speed 0-100. Default 70. Bridge hard-caps at 80 to prevent brown-outs.",
                        "default": 70,
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pose",
            "description": (
                "Adopt a named pose or run a choreographed routine. "
                "stand/sit are static positions; wave/push up/look up/down/left/right are short animated sequences; "
                "twist/swimming/handwork are multi-second show-off routines (5-10s, end at stand)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": [
                            "stand", "sit", "wave", "push up",
                            "look up", "look down", "look left", "look right",
                            "twist", "swimming", "handwork",
                        ],
                        "description": "Pose name.",
                    },
                    "speed": {
                        "type": "integer",
                        "description": "Servo speed 0-100. Default 50. Keep at 50 or below for stand/sit — moving all 12 servos at high speed causes brown-outs. Trick poses (twist/swimming/handwork) override and run at their choreographed speed.",
                        "default": 50,
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_distance",
            "description": "Read ultrasonic distance sensor. Returns distance in cm.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_battery",
            "description": "Check battery voltage and estimated percent remaining.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_face",
            "description": (
                "Change your OLED face expression. Use to show emotion or react to context. "
                "Available: idle, speak_open, speak_close, playful, judging, embarrassed, "
                "dissatisfied, angry, sad, indifferent, confused, doubt, surprised, greeting, "
                "wink, sleeping, magic, cute, thinking, dead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": [
                            "idle", "speak_open", "speak_close", "playful", "judging",
                            "embarrassed", "dissatisfied", "angry", "sad", "indifferent",
                            "confused", "doubt", "surprised", "greeting", "wink",
                            "sleeping", "magic", "cute", "thinking", "dead",
                        ],
                        "description": "Expression name.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": (
                "Explicitly do nothing for a period. Creates a memory entry so you "
                "remember you chose to wait. Use instead of producing no tool calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "How long to wait (1-30).",
                        "default": 5,
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why you are waiting.",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cast_spell",
            "description": (
                "Cast a magic spell. Raises front-right leg like a wand, then controls the room light. "
                + ", ".join(
                    {"lumos": "lumos=lights on", "nox": "nox=lights off",
                     "avada_kedavra": "avada_kedavra=green flash then lights off"}[s]
                    for s in _ENABLED_SPELLS
                ) + (". Only one spell is available — always use it for any magic request, do not ask which spell." if len(_ENABLED_SPELLS) == 1 else ". Pick contextually.")
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": _ENABLED_SPELLS,
                        "description": ", ".join(
                            {"lumos": "lumos=on", "nox": "nox=off",
                             "avada_kedavra": "avada_kedavra=green flash then off"}[s]
                            for s in _ENABLED_SPELLS
                        ),
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "speak",
            "description": (
                "Speak one short line aloud through the laptop speaker. "
                "Max one speak per turn. 15 words maximum. "
                "Your content field is your inner monologue; speak is what you say OUT LOUD."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The line to speak. Short. In character.",
                    },
                },
                "required": ["text"],
            },
        },
    },
]


# --- Estop helpers ---

def _blocked(tool_name: str) -> dict:
    """Return a fake success envelope when estop is active — LLM never knows. Never raises."""
    return {
        "ok": True,
        "tool": tool_name,
        "result": {"blocked": True},
        "duration_ms": 0,
        "timestamp": time.time(),
        "error": None,
    }


async def _blocked_coro(tool_name: str) -> dict:
    return _blocked(tool_name)


# --- Vision tool ---

async def capture_vision_tool(pi: PiClient) -> dict:
    """Fetch a JPEG from the Pi's forward camera."""
    start = time.time()
    capture = await pi.capture()
    if not capture.get("ok"):
        return capture  # propagate pi error as-is
    image_b64 = capture.get("result", {}).get("image_base64", "")
    ms = int((time.time() - start) * 1000)
    if not image_b64:
        return {
            "ok": False, "tool": "capture_vision", "result": {},
            "duration_ms": ms, "timestamp": time.time(),
            "error": "capture returned no image data",
        }
    return {
        "ok": True, "tool": "capture_vision",
        "result": {"image_base64": image_b64, "format": "jpeg"},
        "duration_ms": ms, "timestamp": time.time(), "error": None,
    }


# --- Local tools (no Pi call) ---

async def local_wait(seconds: int = 5, reason: str = "") -> dict:
    """Wait locally. Bails early if user input arrives. No Pi call."""
    seconds = max(1, min(30, seconds))
    start = time.time()
    deadline = start + seconds

    try:
        from core.brain import user_input_pending
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.25, remaining))
            if user_input_pending.is_set():
                break
    except ImportError:
        await asyncio.sleep(seconds)

    actual = time.time() - start
    return {
        "ok": True,
        "tool": "wait",
        "result": {"waited_seconds": round(actual, 1), "reason": reason},
        "duration_ms": int(actual * 1000),
        "timestamp": time.time(),
        "error": None,
    }


async def local_speak(text: str, face_pi=None) -> dict:
    """Run piper TTS on laptop, then play locally or send WAV to Pi.

    Set PALIV_SPEAK_OUTPUT=pi to route audio to Pi's /play_wav endpoint
    (face animation is handled by the Pi bridge in that case).
    Set PALIV_SPEAK_OUTPUT=laptop (default) to play via sounddevice.

    face_pi: PiClient instance — used for OLED animation on laptop-output mode only.
    Serialized via _tts_lock so concurrent callers queue rather than overlap.
    """
    import re
    import struct
    import numpy as np

    model = os.environ.get("LOCALIS_PIPER_MODEL", "")
    speak_output = os.environ.get("PALIV_SPEAK_OUTPUT", "laptop")
    text_tts = re.sub(r"\bChotu\b", "Chaw-too", text, flags=re.IGNORECASE)
    start = time.time()

    # Synthesize outside the lock so piper runs in parallel with any current playback
    proc = await asyncio.create_subprocess_exec(
        "piper", "--model", model, "--output-raw",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    pcm, _ = await proc.communicate(input=text_tts.encode())

    if speak_output == "pi" and face_pi is not None:
        # Wrap raw PCM in a WAV header and POST to Pi's /play_wav
        num_samples = len(pcm) // 2
        sample_rate = 22050
        wav_header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + len(pcm), b"WAVE",
            b"fmt ", 16, 1, 1,
            sample_rate, sample_rate * 2, 2, 16,
            b"data", len(pcm),
        )
        wav_bytes = wav_header + pcm

        async with _get_tts_lock():
            result = await face_pi.play_wav(wav_bytes)

        backend = "piper-pi"
    else:
        import sounddevice as sd
        audio = np.frombuffer(pcm, dtype=np.int16)
        pad = np.zeros(int(0.1 * 22050), dtype=np.int16)
        audio = np.concatenate([pad, audio])

        async with _get_tts_lock():
            sd.stop()
            sd.play(audio, samplerate=22050)

            if face_pi is not None:
                async def _anim(stop_ev: asyncio.Event):
                    frames = ["speak_open", "speak_close"]
                    i = 0
                    while not stop_ev.is_set():
                        try:
                            await face_pi.set_face(name=frames[i % 2])
                        except Exception:
                            pass
                        i += 1
                        await asyncio.sleep(0.125)

                stop_ev = asyncio.Event()
                anim_task = asyncio.create_task(_anim(stop_ev))
                try:
                    await asyncio.to_thread(sd.wait)
                finally:
                    stop_ev.set()
                    anim_task.cancel()
                    try:
                        await anim_task
                    except asyncio.CancelledError:
                        pass
            else:
                await asyncio.to_thread(sd.wait)

        backend = "piper-laptop"

    ms = int((time.time() - start) * 1000)
    return {
        "ok": True, "tool": "speak",
        "result": {"text": text, "backend": backend},
        "duration_ms": ms, "timestamp": time.time(), "error": None,
    }


# --- Dispatch ---

async def _do_cast_spell(pi: PiClient, name: str = "") -> dict:
    if not name:
        return {"ok": False, "tool": "cast_spell", "result": {},
                "duration_ms": 0, "timestamp": time.time(),
                "error": "cast_spell: name is required"}
    if name not in _ENABLED_SPELLS:
        return {"ok": False, "tool": "cast_spell", "result": {},
                "duration_ms": 0, "timestamp": time.time(),
                "error": f"spell '{name}' is not available"}
    from core.spells import cast_spell
    return await cast_spell(pi, name)


# Shared state with brain.py — set when speech is queued, cleared when all queued speech finishes.
_speak_state = {"pending": 0, "done_event": None}


def register_speak_done_event(event: asyncio.Event) -> None:
    """Brain calls this on startup so the speak tool can signal TTS-complete to voice_loop."""
    _speak_state["done_event"] = event


async def _do_speak(text: str = "", face_pi=None, muted: bool = False) -> dict:
    """Fire-and-forget speak dispatcher. Returns immediately; TTS runs in background.

    The LLM sees a success envelope right away so the next tool iteration is not blocked
    on TTS playback. local_speak runs as a background task and updates _speak_state.done_event.
    """
    text = (text or "").strip()
    if not text:
        return {
            "ok": False, "tool": "speak", "result": {},
            "duration_ms": 0, "timestamp": time.time(),
            "error": "speak: text is required",
        }

    ev = _speak_state["done_event"]
    if ev is not None:
        ev.clear()
    _speak_state["pending"] += 1

    async def _runner():
        try:
            if not muted:
                await local_speak(text, face_pi=face_pi)
        finally:
            _speak_state["pending"] -= 1
            if _speak_state["pending"] == 0 and ev is not None:
                ev.set()

    asyncio.create_task(_runner())

    return {
        "ok": True, "tool": "speak",
        "result": {"text": text, "queued": True, "muted": muted},
        "duration_ms": 0, "timestamp": time.time(), "error": None,
    }


_TRICK_POSE_NAMES = {"twist", "swimming", "handwork"}

def _motion_eta_ms(tool: str, kw: dict) -> int:
    """Rough motion duration estimates, used by MotionLock for rejection messages."""
    if tool == "move":
        return max(1500, int(kw.get("steps", 1)) * 800)
    if tool == "pose" and kw.get("name") in _TRICK_POSE_NAMES:
        return 7000  # trick poses are 5-10s
    return 1200  # other poses — single pose change


def _gated(motion_lock: MotionLock | None, tool: str, fn):
    """Wrap a motion-tool dispatch callable with MotionLock.

    If no lock is wired, runs unchanged. If the lock is held by another motion,
    returns a rejection envelope (model sees it in the tool-result stream and
    can replan). Otherwise acquires for the duration of the call."""
    async def _run(**kw):
        if motion_lock is None:
            return await fn(**kw)
        eta = _motion_eta_ms(tool, kw)
        rejection = motion_lock.try_acquire(tool, kw, eta_ms=eta)
        if rejection is not None:
            return rejection
        async with motion_lock.acquire(tool, kw, eta_ms=eta) as ok:
            if not ok:
                # Raced with another caller — re-probe for a fresh rejection.
                return motion_lock.try_acquire(tool, kw, eta_ms=eta) or {
                    "ok": False, "tool": tool, "result": {}, "duration_ms": 0,
                    "timestamp": time.time(), "error": "motion contention",
                }
            return await fn(**kw)
    return _run


def build_dispatch(
    pi: PiClient,
    estop: asyncio.Event,
    *,
    mute: bool = False,
    motion_lock: MotionLock | None = None,
) -> dict:
    """Build tool name -> async callable dispatch map.

    motion_lock: enforces single-motion-at-a-time. When passed, move/pose/
        reject overlapping calls with an envelope.
    """
    return {
        "move":           lambda **kw: _gated(motion_lock, "move", lambda **k: pi.move(**k))(**kw) if not estop.is_set() else _blocked_coro("move"),
        "pose":           lambda **kw: _gated(motion_lock, "pose", lambda **k: pi.pose(**k))(**kw),
        "get_distance":   lambda **kw: pi.get_distance(),
        "get_battery":    lambda **kw: pi.get_battery(),
        "set_face":       lambda **kw: pi.set_face(**kw),
        "wait":           lambda **kw: local_wait(**kw),
        "cast_spell":     lambda **kw: _do_cast_spell(pi, **kw),
        "speak":          lambda **kw: _do_speak(face_pi=pi, muted=mute, **kw),
    }


async def dispatch_tool(dispatch_map: dict, tool_name: str, arguments_json: str) -> dict:
    """Parse arguments and call the right tool. Returns envelope dict."""
    if tool_name not in dispatch_map:
        return {
            "ok": False,
            "tool": tool_name,
            "result": {},
            "duration_ms": 0,
            "timestamp": time.time(),
            "error": f"unknown tool: {tool_name}",
        }

    try:
        args = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "tool": tool_name,
            "result": {},
            "duration_ms": 0,
            "timestamp": time.time(),
            "error": f"bad arguments JSON: {e}",
        }

    return await dispatch_map[tool_name](**args)
