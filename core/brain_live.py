"""Live-mode brain — persistent Gemini Live session with continuous vision.

A sibling to core/brain.py (which remains the stateless turn-based loop).
This module runs a producer/consumer pair against the Backend protocol:

    producer:  input_queue  -->  backend.send_user_text
    consumer:  backend.events()  -->  dispatch_tool  -->  backend.send_tool_result
    sampler:   Pi /stream  -->  backend.send_frame  (1 FPS)

No heartbeat, no memory window, no token estimation — Gemini Live owns
context server-side and we trust its TTL + goAway signals. The brain stops
on SessionEnded / BackendError; reconnect is a v2 concern.

Run with:  PALIV_BRAIN_MODE=live python -m core.brain_live
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
import traceback

from dotenv import load_dotenv
load_dotenv()

from core.backend import AssistantText, BackendError, SessionEnded, ToolCall
from core.frame_sampler import FrameSampler
from core.gemini_live_backend import GeminiLiveBackend
from core.motion_lock import MotionLock
from core.pi_client import PiClient
from core.prompts import load_system_prompt
from core.tools import TOOL_SCHEMAS, build_dispatch, dispatch_tool


PI_HOST = os.getenv("PI_HOST", "http://chotu.local:7000")
MUTE = os.getenv("PALIV_MUTE") == "1"

input_queue: asyncio.Queue = asyncio.Queue()
estop = asyncio.Event()
pi = PiClient(PI_HOST)
motion_lock = MotionLock()


async def producer(backend) -> None:
    """Drain input_queue, forward each text item as a user turn to the backend."""
    while True:
        item = await input_queue.get()
        text = item if isinstance(item, str) else item.get("text", "")
        text = text.strip()
        if not text:
            continue
        print(f"\nyou> {text}")
        try:
            await backend.send_user_text(text)
        except Exception as e:
            print(f"  [send error] {e}")
            traceback.print_exc()


async def consumer(backend, dispatch_map) -> None:
    """Drain backend.events(). Print monologue, dispatch tool calls, reply."""
    async for ev in backend.events():
        if isinstance(ev, AssistantText):
            print(f"chotu> {ev.text}")
        elif isinstance(ev, ToolCall):
            print(f"  [tool] {ev.name}({ev.args})")
            try:
                result = await dispatch_tool(dispatch_map, ev.name, json.dumps(ev.args))
            except Exception as e:
                traceback.print_exc()
                result = {
                    "ok": False, "tool": ev.name, "result": {},
                    "duration_ms": 0, "timestamp": time.time(), "error": str(e),
                }
            status = "ok" if result.get("ok") else f"err: {result.get('error')}"
            print(f"    -> {status}")
            try:
                await backend.send_tool_result(ev.id, result)
            except Exception as e:
                print(f"  [tool-result send error] {e}")
        elif isinstance(ev, SessionEnded):
            print(f"\n[backend] session ended: {ev.reason}")
            return
        elif isinstance(ev, BackendError):
            print(f"\n[backend error] {ev.message}")
            return


async def stdin_loop() -> None:
    """Optional terminal input. Type to inject a user turn."""
    while True:
        try:
            text = await asyncio.to_thread(input, "")
        except EOFError:
            return
        if text.strip():
            input_queue.put_nowait(text)


async def main() -> None:
    system_prompt = load_system_prompt("live")
    print("Chotu live brain starting...")
    print(f"  Pi:    {PI_HOST}")
    print(f"  model: {os.getenv('PALIV_GEMINI_MODEL', 'gemini-3.1-flash-live-preview')}")

    health = await pi.health()
    if health.get("ok"):
        print("  Pi bridge: connected")
    else:
        print(f"  Pi bridge: NOT reachable ({health.get('error', '?')}). Tool calls will error.")

    backend = GeminiLiveBackend(system_prompt=system_prompt, tool_schemas=TOOL_SCHEMAS)
    await backend.start()

    stream_url = PI_HOST.rstrip("/") + "/stream"
    sampler = FrameSampler(backend=backend, stream_url=stream_url, buffer_size=3, sample_hz=1.0)
    await sampler.start()

    dispatch_map = build_dispatch(
        pi, estop, mute=MUTE, motion_lock=motion_lock, frame_sampler=sampler,
    )

    # Wake nudge — first message of the session. The live persona reacts.
    input_queue.put_nowait("[system] You are awake. Live your life.")

    loop = asyncio.get_running_loop()
    _shutdown = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, _shutdown.set)
    loop.add_signal_handler(signal.SIGTERM, _shutdown.set)

    tasks = [
        asyncio.create_task(producer(backend), name="producer"),
        asyncio.create_task(consumer(backend, dispatch_map), name="consumer"),
        asyncio.create_task(stdin_loop(), name="stdin"),
    ]
    _stop_task = asyncio.create_task(_shutdown.wait())

    try:
        await asyncio.wait(tasks + [_stop_task], return_when=asyncio.FIRST_COMPLETED)
    finally:
        print("\n[live] shutting down...")
        for t in tasks + [_stop_task]:
            t.cancel()
        await asyncio.gather(*tasks, _stop_task, return_exceptions=True)
        try:
            await sampler.stop()
        except Exception:
            pass
        try:
            await backend.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(pi.pose("sit"), timeout=5.0)
        except Exception:
            pass
        await pi.close()
        print("bye.")


if __name__ == "__main__":
    asyncio.run(main())
