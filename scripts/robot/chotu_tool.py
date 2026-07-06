"""
Chotu tool bridge for the CC Chotu skill.

Usage: python -m scripts.robot.chotu_tool <command> ['<json_args>']

Commands:
  state                   — live battery + distance + memory summary
  memory_read             — recent turns formatted for skill injection
  memory_append '<json>'  — append a turn {"content":"...","tools":[...],"speak":"..."}

  move '{"direction":"forward","steps":1}'
  pose '{"name":"sit"}'
  do_trick '{"name":"wave"}'
  speak '{"text":"..."}'
  capture_vision          — saves JPEG to /tmp/chotu_capture.jpg
  get_distance
  get_battery
  get_perception '{"color":"red","face":true,"human":true}'
  wait '{"seconds":2}'
  log '{"message":"..."}'
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

MEMORY_PATH = Path("/tmp/chotu_cc_context.json")
CAPTURE_PATH = Path("/tmp/chotu_capture.jpg")
MEMORY_MAX_TURNS = 15
PI_HOST = os.getenv("PI_HOST", "http://chotu.local:7000")


# --- Memory ---

def _load_memory() -> list[dict]:
    if MEMORY_PATH.exists():
        try:
            return json.loads(MEMORY_PATH.read_text())
        except Exception:
            pass
    return []


def _save_memory(turns: list[dict]) -> None:
    MEMORY_PATH.write_text(json.dumps(turns[-MEMORY_MAX_TURNS:], indent=2))


def cmd_memory_read() -> None:
    turns = _load_memory()
    if not turns:
        print("(no memory — first session)")
        return
    for i, t in enumerate(turns):
        idx = i - len(turns)
        ts = datetime.fromtimestamp(t.get("ts", 0)).strftime("%H:%M:%S")
        content = t.get("content", "")
        tools = t.get("tools", [])
        spoken = t.get("speak", "")
        line = f"[tick {idx:+d} | {ts}] {content}"
        if tools:
            line += f"  ← {', '.join(tools)}"
        if spoken:
            line += f'  (spoke: "{spoken}")'
        print(line)


def cmd_memory_append(raw: str) -> None:
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        entry = {"content": raw}
    entry.setdefault("ts", time.time())
    turns = _load_memory()
    turns.append(entry)
    _save_memory(turns)
    from core import trace
    trace.record("thought", "monologue", {}, {}, thought=entry.get("content", ""))
    print(f"ok — {len(turns)} turns in memory")


# --- Async tool dispatch ---

async def _run(command: str, args: dict) -> dict | str:
    from core.pi_client import PiClient

    pi = PiClient(PI_HOST)
    try:
        if command == "state":
            return await _cmd_state(pi)
        elif command == "move":
            return await pi.move(
                direction=args.get("direction", "forward"),
                steps=int(args.get("steps", 1)),
                speed=int(args.get("speed", 60)),
            )
        elif command == "pose":
            return await pi.pose(
                name=args.get("name", "stand"),
                speed=int(args.get("speed", 50)),
            )
        elif command == "do_trick":
            return await pi.do_trick(
                name=args.get("name", ""),
                speed=int(args.get("speed", 80)),
            )
        elif command == "speak":
            return await _cmd_speak(args.get("text", ""), pi=pi)
        elif command == "capture_vision":
            return await _cmd_capture(pi)
        elif command == "get_distance":
            return await pi.get_distance()
        elif command == "get_battery":
            return await pi.get_battery()
        elif command == "get_perception":
            return await pi.get_perception(
                color=args.get("color"),
                face=bool(args.get("face", False)),
                human=bool(args.get("human", False)),
            )
        elif command == "wait":
            secs = float(args.get("seconds", 1))
            await asyncio.sleep(secs)
            return {"ok": True, "tool": "wait", "result": {"seconds": secs},
                    "duration_ms": int(secs * 1000), "timestamp": time.time(), "error": None}
        elif command == "set_face":
            return await pi.set_face(name=args.get("name", "idle"))
        elif command == "set_light":
            from core.spells import cast_spell
            return await cast_spell(pi, args.get("spell", "lumos"))
        elif command == "log":
            msg = args.get("message", "")
            print(f"[log] {msg}")
            return {"ok": True, "tool": "log", "result": {"message": msg},
                    "duration_ms": 0, "timestamp": time.time(), "error": None}
        elif command == "wait_for_event":
            return await _cmd_wait_for_event(float(args.get("timeout", 300)))
        else:
            return {"ok": False, "tool": command, "result": {}, "duration_ms": 0,
                    "timestamp": time.time(), "error": f"unknown command: {command}"}
    finally:
        await pi.close()


async def _cmd_state(pi) -> str:
    turns = _load_memory()
    lines = []
    bat = await pi.get_battery()
    if bat.get("ok"):
        r = bat.get("result", {})
        lines.append(f"battery: {r.get('percent', '?')}% ({r.get('voltage', '?')}V)")
    else:
        lines.append(f"battery: unreachable ({bat.get('error', '?')})")
    dist = await pi.get_distance()
    if dist.get("ok"):
        cm = dist.get("result", {}).get("cm", -1)
        lines.append(f"distance: {cm}cm" if cm > 0 else "distance: sensor unreliable")
    else:
        lines.append("distance: unreachable")
    lines.append(f"memory: {len(turns)} turns recorded")
    return "\n".join(lines)


async def _cmd_speak(text: str, pi=None) -> dict:
    text = text.strip()
    if not text:
        return {"ok": False, "tool": "speak", "result": {}, "duration_ms": 0,
                "timestamp": time.time(), "error": "speak: text is empty"}
    print(f'[speaks] "{text}"')
    if pi is not None:
        # Use Pi's onboard speaker via /speak (espeak)
        return await pi.speak(text)
    from core.tools import local_speak
    return await local_speak(text, face_pi=None)


async def _cmd_capture(pi) -> dict:
    result = await pi.capture()
    if not result.get("ok"):
        return result
    r = result.get("result", {})
    b64 = r.get("image_base64") or r.get("image_b64") or r.get("jpeg_b64") or ""
    if b64:
        jpeg_bytes = base64.b64decode(b64)
        CAPTURE_PATH.write_bytes(jpeg_bytes)
        from core import trace
        frame_rel = trace.save_frame(jpeg_bytes)
        result = dict(result)
        result["result"] = {k: v for k, v in r.items()
                            if k not in ("image_base64", "image_b64", "jpeg_b64")}
        result["result"]["image_saved"] = str(CAPTURE_PATH)
        result["result"]["trace_frame"] = frame_rel
    return result


async def _cmd_wait_for_event(timeout: float) -> dict:
    """Block until text / wake-word / speech / timeout, whichever fires first.
    Text channel: a line written to $PALIV_WAIT_INPUT (default /tmp/chotu_wait_input).
    Voice channel is gated by PALIV_VOICE=1."""
    t0 = time.time()
    text_path = Path(os.getenv("PALIV_WAIT_INPUT", "/tmp/chotu_wait_input"))
    voice_on = os.getenv("PALIV_VOICE", "0") == "1"

    async def watch_text():
        if text_path.exists():        # clear stale content so we only react to new lines
            text_path.unlink()
        while True:
            if text_path.exists():
                line = text_path.read_text().strip()
                try:
                    text_path.unlink()
                except FileNotFoundError:
                    pass
                if line:
                    return ("text", line)
            await asyncio.sleep(0.2)

    tasks = [asyncio.create_task(watch_text())]
    if voice_on:
        from core.voice import wait_for_wake_or_speech  # returns (kind, transcript)
        tasks.append(asyncio.create_task(wait_for_wake_or_speech()))

    done, pending = await asyncio.wait(
        tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
    )
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    if not done:
        return {"event": "timeout", "text": None, "waited_s": round(time.time() - t0, 1)}
    kind, text = next(iter(done)).result()
    return {"event": kind, "text": text, "waited_s": round(time.time() - t0, 1)}


# --- Entry point ---

def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("--help", "-h"):
        print(__doc__)
        return

    command = args[0]

    from core import trace
    if not os.getenv("PALIV_TRACE_DIR"):
        os.environ["PALIV_TRACE_DIR"] = str(trace.session_dir(os.getenv("PALIV_RUNNER", "fable")))

    if command == "memory_read":
        cmd_memory_read()
        return

    if command == "memory_append":
        raw = args[1] if len(args) > 1 else "{}"
        cmd_memory_append(raw)
        return

    tool_args = {}
    if len(args) > 1:
        try:
            tool_args = json.loads(args[1])
        except json.JSONDecodeError:
            print(f"error: invalid JSON: {args[1]!r}", file=sys.stderr)
            sys.exit(1)

    result = asyncio.run(_run(command, tool_args))

    if command != "wait_for_event":
        kind = trace.classify(command)
        frame = result.get("result", {}).get("trace_frame") if isinstance(result, dict) else None
        trace.record(kind, command, tool_args, result if isinstance(result, dict) else {"text": result}, frame=frame)

    if isinstance(result, str):
        print(result)
    elif command == "wait_for_event":
        print(json.dumps(result))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
