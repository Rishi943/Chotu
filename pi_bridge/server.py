#!/usr/bin/env python3
"""Chotu Pi bridge — dumb FastAPI server wrapping PiCrawler hardware.

Run with: sudo ~/chotu-bridge/.venv/bin/python3 server.py
Requires sudo for GPIO / robot_hat access.

--no-camera (or CHOTU_NO_CAMERA=1) skips vilib's camera init, which is most of a
cold start. The camera can be turned on later without restarting: POST /camera
{"on": true}, or just call /capture, /perception or /stream and it starts itself.
"""

import asyncio
import base64
import logging
import os
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Suppress uvicorn access-log noise for high-frequency poll endpoints.
class _PollFilter(logging.Filter):
    _MUTED = {"/distance", "/health", "/battery", "/stream"}
    def filter(self, record):
        msg = record.getMessage()
        return not any(p in msg for p in self._MUTED)

logging.getLogger("uvicorn.access").addFilter(_PollFilter())

import cv2
import pygame
import robot_hat
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from picrawler import Picrawler
from robot_hat import Ultrasonic, Pin, ADC, Music

from sequence import play_frames

_music = Music()
from vilib import Vilib

# ---------------------------------------------------------------------------
# Hardware init — reset_mcu() must come first, before any robot_hat use
# ---------------------------------------------------------------------------

robot_hat.reset_mcu()
time.sleep(0.2)

crawler = Picrawler()


def _play_frames(frames, cap=None, speed_override=None):
    """Server-side shortcut binding play_frames to the live crawler (used by
    /play_sequence and add-chotu-tool's generated _play_{tool}). cap defaults to
    MAX_MOTION_SPEED, resolved at call time (it's defined later in this module)."""
    play_frames(crawler, frames, MAX_MOTION_SPEED if cap is None else cap, speed_override)


us = Ultrasonic(Pin("D2"), Pin("D3"))

# Shared latest JPEG frame for /stream consumers (laptop FrameSampler).
# Re-encoded ~10 FPS by a background grab loop in lifespan.
_latest_frame_jpeg: bytes | None = None
_latest_frame_lock = asyncio.Lock()
_FRAME_GRAB_HZ = 10.0
_FRAME_GRAB_QUALITY = 80

# Camera is the slow half of startup (~40 s of a cold boot). It can be left off
# with --no-camera / CHOTU_NO_CAMERA=1 and started later — on demand by any
# endpoint that needs a frame, or explicitly via POST /camera {"on": true}.
_camera_running = False
_camera_lock = asyncio.Lock()
_grab_task: asyncio.Task | None = None
_CAMERA_SETTLE_S = 2.0
_START_WITH_CAMERA = not (
    os.environ.get("CHOTU_NO_CAMERA") == "1" or "--no-camera" in sys.argv
)

_LOWLIGHT_MEAN_THRESHOLD = 60     # 0-255 gray mean; below this, boost
_BOOST_EXPOSURE_US = 100_000      # 100 ms shutter — bright in a very dark room
_BOOST_ANALOGUE_GAIN = 8.0        # IMX708 max analogue gain ~= 8-12; 8 is safe
_BOOST_SETTLE_S = 0.8             # manual controls take ~3-5 frames to apply;
                                  # at 100ms exposure that's ~0.5s + margin

_bat_adc = ADC("A4")
pygame.mixer.init()  # must run before speak uses pygame.mixer.Sound

from chotu import face as _face
_face.init()


def _read_battery_voltage() -> float:
    """Read battery via ADC A4 with 3× voltage divider (robot_hat standard)."""
    return round(_bat_adc.read_voltage() * 3, 2)


def _voltage_to_percent(v: float) -> int:
    """Map 2S LiPo range (6.0–8.4 V) to 0–100%."""
    return max(0, min(100, int((v - 6.0) / (8.4 - 6.0) * 100)))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

async def _grab_loop():
    """Re-encode the newest Vilib frame for /stream consumers."""
    global _latest_frame_jpeg
    grab_period = 1.0 / _FRAME_GRAB_HZ
    while True:
        try:
            frame = Vilib.img
            if frame is not None:
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _FRAME_GRAB_QUALITY])
                if ok:
                    async with _latest_frame_lock:
                        _latest_frame_jpeg = buf.tobytes()
        except Exception as e:
            logging.warning(f"frame grab error: {e}")
        await asyncio.sleep(grab_period)


async def camera_on() -> bool:
    """Start the camera if it isn't already. ~3 s (vilib init + AE/AWB settle).
    Returns True if this call started it. Safe to call from any endpoint."""
    global _camera_running, _grab_task
    async with _camera_lock:
        if _camera_running:
            return False
        t0 = time.time()
        Vilib.camera_start(vflip=False, hflip=False)
        await asyncio.sleep(_CAMERA_SETTLE_S)  # AE/AWB initial convergence
        _grab_task = asyncio.create_task(_grab_loop(), name="frame-grab")
        _camera_running = True
        logging.info(f"camera on ({int((time.time()-t0)*1000)}ms)")
        return True


async def camera_off() -> bool:
    """Stop the camera and the frame-grab loop. Returns True if it was running."""
    global _camera_running, _grab_task, _latest_frame_jpeg
    async with _camera_lock:
        if not _camera_running:
            return False
        if _grab_task is not None:
            _grab_task.cancel()
            try:
                await _grab_task
            except (asyncio.CancelledError, Exception):
                pass
            _grab_task = None
        Vilib.camera_close()
        async with _latest_frame_lock:
            _latest_frame_jpeg = None
        _camera_running = False
        logging.info("camera off")
        return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _START_WITH_CAMERA:
        await camera_on()
    else:
        logging.info("camera left off at startup (--no-camera); "
                     "it starts on demand or via POST /camera")
    _face.set_face("greeting")
    try:
        crawler.do_step("stand", 40)
        await asyncio.sleep(1.0)
        logging.info("Startup pose: stand")
    except Exception as e:
        logging.warning(f"Startup stand failed: {e}")

    try:
        yield
    finally:
        await camera_off()
        _face.set_face("sleeping")


app = FastAPI(lifespan=lifespan)


def _envelope(tool: str, result: dict, start: float, error=None) -> dict:
    return {
        "ok": error is None,
        "tool": tool,
        "result": result,
        "duration_ms": int((time.time() - start) * 1000),
        "timestamp": time.time(),
        "error": error,
    }


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class MoveRequest(BaseModel):
    direction: str   # "forward" | "backward" | "turn left" | "turn right"
    steps: int = 1
    speed: int = 70


# The exact strings picrawler's do_action accepts for locomotion. "left"/"right"
# are NOT among them — see /move.
MOVE_DIRECTIONS = {"forward", "backward", "turn left", "turn right"}


class CameraRequest(BaseModel):
    on: bool


class PoseRequest(BaseModel):
    name: str        # "stand" | "sit" | "wave" | "push up" | "look up/down/left/right" | "twist" | "swimming" | "handwork"
    speed: int = 50

MAX_POSE_SPEED = 40   # stand/sit move all 12 servos simultaneously — cap to avoid current spike
MAX_MOTION_SPEED = 90  # hard cap for move/set_legs — peak current safety (was 60; raised to 90)
MAX_TRICK_SPEED = 100  # tricks are pre-choreographed; official examples use 100
MOTION_COOLDOWN_S = 0.6  # min gap between motion calls; lets pack voltage recover from sag (was 0.3)

# Static positions available as do_step presets; animated poses (wave, look*,
# push up) go through do_action; trick poses are multi-second choreographed
# routines that run through the _TRICKS dispatch table at MAX_TRICK_SPEED.
_STATIC_POSES = {"stand", "sit"}
_TRICK_POSES = {"twist", "swimming", "handwork"}

# Serializes all motion endpoints. The crawler is a single hardware singleton; two
# concurrent do_action/do_step calls from different threads corrupt servo state and
# spike current draw. Brain may dispatch multiple tool calls in parallel via
# asyncio.gather — the lock forces them to run one at a time on the bridge.
_motion_lock: asyncio.Lock | None = None  # lazy-init in _motion_section (event loop must exist)
_last_motion_end: float = 0.0


def _get_motion_lock() -> asyncio.Lock:
    global _motion_lock
    if _motion_lock is None:
        _motion_lock = asyncio.Lock()
    return _motion_lock


@asynccontextmanager
async def _motion_section():
    """Hold the motion lock and enforce MOTION_COOLDOWN_S since the last motion ended.
    Updates _last_motion_end on exit (success or failure)."""
    global _last_motion_end
    lock = _get_motion_lock()
    async with lock:
        gap = time.monotonic() - _last_motion_end
        if gap < MOTION_COOLDOWN_S:
            await asyncio.sleep(MOTION_COOLDOWN_S - gap)
        try:
            yield
        finally:
            _last_motion_end = time.monotonic()


class SpeakRequest(BaseModel):
    text: str


class SetLegsRequest(BaseModel):
    legs: list[list[float]]  # 4 × [x, y, z] in mm
    speed: int = 70


class PlaySequenceRequest(BaseModel):
    frames: list             # [{legs: 4×[x,y,z], speed, hold_s}]
    speed: int | None = None  # optional override applied to every frame


class PeekOverRequest(BaseModel):
    lead: str               # "left" | "right" — which front leg freezes mid-air
    reach: str = "shallow"  # "shallow" | "deep"
    pause_s: float = 1.5    # hold time at the frozen frame
    speed: int = 60


class FaceRequest(BaseModel):
    name: str


class TrickRequest(BaseModel):
    name: str        # "pushup" | "twist" | "swimming" | "handwork"
    speed: int = 70


VILIB_COLORS = {"red", "orange", "yellow", "green", "blue", "purple"}


class PerceptionRequest(BaseModel):
    color: str | None = None
    face: bool = False
    human: bool = False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/face")
async def set_face_endpoint(req: FaceRequest):
    start = time.time()
    ok = _face.set_face(req.name)
    return _envelope("face", {"name": req.name, "ok": ok}, start)


@app.get("/health")
async def health():
    start = time.time()
    return _envelope("health", {"status": "ok", "camera": _camera_running}, start)


@app.post("/camera")
async def camera(req: CameraRequest):
    """Turn the camera on or off without restarting the bridge."""
    start = time.time()
    logging.info(f"POST /camera  on={req.on}")
    try:
        changed = await (camera_on() if req.on else camera_off())
        return _envelope("camera", {"on": _camera_running, "changed": changed}, start)
    except Exception as e:
        logging.error(f"  camera error: {e}")
        return _envelope("camera", {"on": _camera_running}, start, str(e))


@app.post("/move")
async def move(req: MoveRequest):
    start = time.time()
    speed = min(req.speed, MAX_MOTION_SPEED)
    logging.info(f"POST /move  direction={req.direction} steps={req.steps} speed={speed}")
    if req.direction not in MOVE_DIRECTIONS:
        # picrawler's do_action prints "No such action" and returns silently, so
        # an unknown direction used to come back ok:true after doing nothing.
        msg = f"unknown direction: {req.direction!r} (expected one of {sorted(MOVE_DIRECTIONS)})"
        logging.error(f"  move error: {msg}")
        return _envelope("move", {
            "direction": req.direction,
            "steps_requested": req.steps,
            "steps_completed": 0,
            "halted_early": True,
        }, start, msg)
    try:
        async with _motion_section():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: crawler.do_action(req.direction, req.steps, speed),
            )
        result = _envelope("move", {
            "direction": req.direction,
            "steps_requested": req.steps,
            "steps_completed": req.steps,
            "halted_early": False,
        }, start)
        logging.info(f"  move ok ({result['duration_ms']}ms)")
        return result
    except Exception as e:
        logging.error(f"  move error: {e}")
        return _envelope("move", {
            "direction": req.direction,
            "steps_requested": req.steps,
            "steps_completed": 0,
            "halted_early": True,
        }, start, str(e))


@app.post("/pose")
async def pose(req: PoseRequest):
    start = time.time()
    is_trick = req.name in _TRICK_POSES
    # Trick poses are pre-choreographed and need the higher speed cap to look right.
    speed = min(req.speed, MAX_TRICK_SPEED if is_trick else MAX_POSE_SPEED)
    logging.info(f"POST /pose  name={req.name} speed={speed}")
    try:
        async with _motion_section():
            loop = asyncio.get_event_loop()
            if is_trick:
                await loop.run_in_executor(None, lambda: _TRICKS[req.name](speed))
            elif req.name in _STATIC_POSES:
                await loop.run_in_executor(None, lambda: crawler.do_step(req.name, speed))
            else:
                await loop.run_in_executor(None, lambda: crawler.do_action(req.name, 1, speed))
            await asyncio.sleep(0.1)  # let servos settle before next command
        held_ms = int((time.time() - start) * 1000)
        result = _envelope("pose", {"pose": req.name, "held_ms": held_ms}, start)
        logging.info(f"  pose ok ({held_ms}ms)")
        return result
    except Exception as e:
        logging.error(f"  pose error: {e}")
        return _envelope("pose", {"pose": req.name, "held_ms": 0}, start, str(e))


@app.post("/set_legs")
async def set_legs(req: SetLegsRequest):
    start = time.time()
    speed = min(req.speed, MAX_MOTION_SPEED)
    logging.info(f"POST /set_legs  legs={req.legs} speed={speed}")
    try:
        if len(req.legs) != 4 or any(len(leg) != 3 for leg in req.legs):
            raise ValueError("legs must be 4 × [x, y, z]")
        async with _motion_section():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: crawler.do_step(req.legs, speed),
            )
        result = _envelope("set_legs", {"legs": req.legs, "speed": speed}, start)
        logging.info(f"  set_legs ok ({result['duration_ms']}ms)")
        return result
    except Exception as e:
        logging.error(f"  set_legs error: {e}")
        return _envelope("set_legs", {"legs": req.legs, "speed": speed}, start, str(e))


@app.post("/play_sequence")
async def play_sequence(req: PlaySequenceRequest):
    start = time.time()
    bad = (not req.frames) or any(
        not isinstance(f.get("legs"), list) or len(f["legs"]) != 4
        or any(not isinstance(leg, list) or len(leg) != 3 for leg in f["legs"])
        for f in req.frames
    )
    if bad:
        return _envelope("play_sequence", {"frames": len(req.frames or [])}, start,
                         "each frame needs 4 legs of [x,y,z]")
    logging.info(f"POST /play_sequence  frames={len(req.frames)} speed={req.speed}")
    try:
        async with _motion_section():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: _play_frames(req.frames, MAX_MOTION_SPEED, req.speed))
        return _envelope("play_sequence",
                         {"frames": len(req.frames), "halted_early": False}, start)
    except Exception as e:
        logging.error(f"  play_sequence error: {e}")
        return _envelope("play_sequence",
                         {"frames": len(req.frames), "halted_early": True}, start, str(e))


# Front-left (leg 2) leads — the picrawler forward-gait parity-0 mid-step frame.
_PEEK_FREEZE_LEFT = {
    "shallow": [[45, 45, -50], [70, 0, -30], [45, 0, -50], [45, 45, -50]],
    "deep":    [[45, 45, -50], [45, 90, -30], [45, 0, -50], [45, 45, -50]],
}
# Retract the reaching foot and raise both front feet to Z_UP so the nose dips and
# weight shifts rearward (no full gait step). Symmetric — same for both leads.
_PEEK_LEAN_BACK = [[45, 45, -30], [45, 0, -30], [45, 0, -50], [45, 45, -50]]


def _peek_over_poses(lead: str, reach: str):
    """Return (freeze_pose, lean_back_pose). lead: left|right, reach: shallow|deep."""
    if lead not in ("left", "right"):
        raise ValueError(f"lead must be 'left' or 'right', got {lead!r}")
    if reach not in ("shallow", "deep"):
        raise ValueError(f"reach must be 'shallow' or 'deep', got {reach!r}")
    left = _PEEK_FREEZE_LEFT[reach]
    if lead == "left":
        freeze = [list(c) for c in left]
    else:  # parity-1 transform: swap legs 1<->2 and 3<->4
        freeze = [list(left[1]), list(left[0]), list(left[3]), list(left[2])]
    return freeze, [list(c) for c in _PEEK_LEAN_BACK]


def _peek_over_blocking(lead: str, reach: str, pause_s: float, speed: int) -> None:
    freeze, lean_back = _peek_over_poses(lead, reach)   # validates lead/reach
    crawler.do_step("stand", 40)
    crawler.do_step(freeze, speed)          # reach + lift the chosen front leg
    time.sleep(pause_s)                     # hold the mid-step frame
    crawler.do_step(lean_back, speed)       # recoil: weight shifts back
    crawler.do_action("look up", 1, speed)  # end holding the look-up
    crawler.stand_position = 0              # reset gait parity for later move calls


@app.post("/peek_over")
async def peek_over(req: PeekOverRequest):
    start = time.time()
    speed = min(req.speed, MAX_MOTION_SPEED)
    pause_s = max(0.0, min(req.pause_s, 10.0))  # bound the held sleep — it runs under the motion lock
    logging.info(f"POST /peek_over  lead={req.lead} reach={req.reach} pause_s={pause_s} speed={speed}")
    try:
        async with _motion_section():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: _peek_over_blocking(req.lead, req.reach, pause_s, speed),
            )
        result = _envelope("peek_over", {
            "lead": req.lead, "reach": req.reach, "pause_s": pause_s,
        }, start)
        logging.info(f"  peek_over ok ({result['duration_ms']}ms)")
        return result
    except Exception as e:
        logging.error(f"  peek_over error: {e}")
        return _envelope("peek_over", {"lead": req.lead, "reach": req.reach}, start, str(e))


@app.post("/play_wav")
async def play_wav(request: Request):
    """Accept raw WAV bytes from laptop (piper output) and play via pygame."""
    start = time.time()
    data = await request.body()
    logging.info(f"POST /play_wav  bytes={len(data)}")
    try:
        loop = asyncio.get_event_loop()

        def _do_play():
            _face.start_speak_animation()
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(data)
                    tmpfile = f.name
                try:
                    _music.sound_play(tmpfile)
                finally:
                    try:
                        os.unlink(tmpfile)
                    except OSError:
                        pass
            finally:
                _face.stop_speak_animation()

        await loop.run_in_executor(None, _do_play)
        result = _envelope("play_wav", {"bytes": len(data), "played": True}, start)
        logging.info(f"  play_wav ok ({result['duration_ms']}ms)")
        return result
    except Exception as e:
        logging.error(f"  play_wav error: {e}")
        return _envelope("play_wav", {"bytes": len(data), "played": False}, start, str(e))


@app.post("/speak")
async def speak(req: SpeakRequest):
    start = time.time()
    logging.info(f"POST /speak  text={req.text!r}")
    try:
        loop = asyncio.get_event_loop()

        def _do_speak():
            _face.start_speak_animation()
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    tmpfile = f.name
                try:
                    subprocess.run(["espeak", "-w", tmpfile, "-v", "en", req.text], check=True, timeout=30)
                    _music.sound_play(tmpfile)
                finally:
                    try:
                        os.unlink(tmpfile)
                    except OSError:
                        pass
            finally:
                _face.stop_speak_animation()

        await loop.run_in_executor(None, _do_speak)
        result = _envelope("speak", {"text": req.text, "played": True}, start)
        logging.info(f"  speak ok ({result['duration_ms']}ms)")
        return result
    except Exception as e:
        logging.error(f"  speak error: {e}")
        return _envelope("speak", {"text": req.text, "played": False}, start, str(e))


@app.get("/distance")
async def distance():
    start = time.time()
    try:
        cm = float(us.read())
        reliable = 2.0 <= cm <= 300.0
        return _envelope("get_distance", {"cm": round(cm, 1), "reliable": reliable}, start)
    except Exception as e:
        logging.error(f"  distance error: {e}")
        return _envelope("get_distance", {"cm": 0, "reliable": False}, start, str(e))


def _boost_and_grab() -> "np.ndarray | None":
    """One-shot long exposure. Always restores auto AE."""
    try:
        Vilib.set_controls({
            "AeEnable": False,
            "ExposureTime": _BOOST_EXPOSURE_US,
            "AnalogueGain": _BOOST_ANALOGUE_GAIN,
        })
        time.sleep(_BOOST_SETTLE_S)   # let boosted frames reach Vilib.img
        return Vilib.img
    finally:
        Vilib.set_controls({"AeEnable": True})


class CaptureRequest(BaseModel):
    full: bool = False


@app.post("/capture")
async def capture(req: CaptureRequest = CaptureRequest()):
    start = time.time()
    try:
        await camera_on()   # no-op if already running; ~3 s if the bridge started --no-camera
        frame = Vilib.img
        if frame is None:
            logging.warning("  capture: no frame available")
            return _envelope("capture", {}, start, "no frame available from camera")
        boosted = False
        mean = float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())
        if mean < _LOWLIGHT_MEAN_THRESHOLD:
            logging.info(f"  capture: low light (mean={mean:.0f}), boosting")
            loop = asyncio.get_running_loop()
            bframe = await loop.run_in_executor(None, _boost_and_grab)
            if bframe is not None:
                frame, boosted = bframe, True
        if req.full:
            out, label = frame, f"{frame.shape[1]}x{frame.shape[0]}q90"
            quality = 90
        else:
            out, label = cv2.resize(frame, (320, 240), interpolation=cv2.INTER_LINEAR), "320x240q40"
            quality = 40
        _, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, quality])
        image_b64 = base64.b64encode(buf).decode()
        kb = len(image_b64) * 3 // 4 // 1024
        logging.info(f"  capture ok (~{kb}KB {label}, {int((time.time()-start)*1000)}ms)")
        return _envelope("capture", {"image_base64": image_b64, "boosted": boosted}, start)
    except Exception as e:
        logging.error(f"  capture error: {e}")
        return _envelope("capture", {}, start, str(e))


@app.get("/battery")
async def battery():
    start = time.time()
    try:
        voltage = _read_battery_voltage()
        percent = _voltage_to_percent(voltage)
        return _envelope("battery", {"voltage": voltage, "percent": percent, "charging": False}, start)
    except Exception as e:
        logging.error(f"  battery error: {e}")
        return _envelope("battery", {}, start, str(e))


@app.post("/perception")
async def perception(req: PerceptionRequest):
    start = time.time()
    try:
        await camera_on()   # perception is meaningless without frames
        result = {}

        if req.color:
            if req.color not in VILIB_COLORS:
                return _envelope("perception", {}, start, f"unsupported color: {req.color}")
            Vilib.color_detect(req.color)
            await asyncio.sleep(0.1)
            p = Vilib.detect_obj_parameter
            result["color"] = {
                "target": req.color,
                "detected": p.get("color_n", 0) > 0,
                "x": p.get("color_x", 0),
                "y": p.get("color_y", 0),
                "size": p.get("color_n", 0),
            }

        if req.face:
            Vilib.face_detect_switch(True)
            await asyncio.sleep(0.1)
            p = Vilib.detect_obj_parameter
            result["face"] = {
                "detected": p.get("human_n", 0) > 0,
                "x": p.get("human_x", 0),
                "y": p.get("human_y", 0),
            }

        if req.human:
            p = Vilib.detect_obj_parameter
            result["human"] = {
                "detected": p.get("human_n", 0) > 0,
            }

        return _envelope("perception", result, start)
    except Exception as e:
        return _envelope("perception", {}, start, str(e))


# ---------------------------------------------------------------------------
# Tricks
# ---------------------------------------------------------------------------

def _trick_pushup(speed: int):
    up   = [[80, 0, -100], [80, 0, -100], [0, 120, -60], [0, 120, -60]]
    down = [[80, 0, -30],  [80, 0, -30],  [0, 120, -60], [0, 120, -60]]
    for _ in range(2):
        crawler.do_step(down, speed); time.sleep(0.6)
        crawler.do_step(up, speed);   time.sleep(0.6)
    crawler.do_step("stand", 40)


def _trick_twist(speed: int):
    new_step = [[50, 50, -80], [50, 50, -80], [50, 50, -80], [50, 50, -80]]
    for _ in range(2):
        for i in range(4):
            for inc in range(30, 60, 5):
                rise = [50, 50, -80 + inc * 0.5]
                drop = [50, 50, -80 - inc]
                new_step[i]           = rise
                new_step[(i + 2) % 4] = drop
                new_step[(i + 1) % 4] = rise
                new_step[(i - 1) % 4] = drop
                crawler.do_step(new_step, speed)
                time.sleep(0.03)
    crawler.do_step("stand", 40)


def _trick_swimming(speed: int, loops: int = 40):
    # Ramp to start position slowly so we don't spike current from wherever legs are.
    crawler.do_step([[60, 0, -30]] * 4, 40)
    time.sleep(0.8)
    crawler.do_step([[80, 20, -20], [80, 20, -20], [40, 60, -50], [40, 60, -50]], 40)
    time.sleep(0.8)
    for i in range(loops):
        phase = i / loops  # 0.0 → 1.0
        front_x = 80 + 20 * phase
        front_y = 20 + 20 * phase
        front_z = -20 + 10 * phase
        rear_x = 40 - 20 * phase
        rear_y = 60 + 40 * phase
        rear_z = -50 + 20 * phase
        crawler.do_step(
            [[front_x, front_y, front_z], [front_x, front_y, front_z],
             [rear_x, rear_y, rear_z], [rear_x, rear_y, rear_z]],
            speed,
        )
        time.sleep(0.05)
    crawler.do_step("stand", 40)


def _trick_handwork(speed: int):
    base = None
    try:
        base = crawler.move_list["sit"][0]
    except Exception:
        pass
    if not base or len(base) < 4:
        crawler.do_step("sit", speed)
        time.sleep(0.6)
        return
    left_hand = crawler.mix_step(base, 0, [0, 50, 80])
    right_hand = crawler.mix_step(base, 1, [0, 50, 80])
    two_hand   = crawler.mix_step(left_hand, 1, [0, 50, 80])
    crawler.do_step("sit", speed);       time.sleep(0.6)
    crawler.do_step(left_hand, speed);   time.sleep(0.6)
    crawler.do_step(two_hand, speed);    time.sleep(0.6)
    crawler.do_step(right_hand, speed);  time.sleep(0.6)
    crawler.do_step("sit", speed);       time.sleep(0.6)
    crawler.do_step("stand", 40)


def _trick_fluid_swim(speed: int, cycles: int = 5):
    import math
    FRAMES = 32  # frames per cycle
    DT = 0.022   # ~45 fps on-Pi

    crawler.do_step([[60, 0, -60]] * 4, 35)
    time.sleep(0.6)

    for i in range(cycles * FRAMES):
        t = (i / FRAMES) * 2 * math.pi
        def lp(phase):
            x = round(60 + 22 * math.sin(phase), 1)
            z = round(-60 + 20 * math.sin(phase + math.pi / 2), 1)
            return [x, 0, z]
        # diagonal gait: FL+RR in phase, FR+RL antiphase
        crawler.do_step([lp(t), lp(t + math.pi), lp(t + math.pi), lp(t)], speed)
        time.sleep(DT)

    crawler.do_step("stand", 40)


def _trick_wand(speed: int):
    """Raise left-front leg (index 1) to wand position: up, hold, down.

    Uses leg 1 (same stable side as wave) so the tripod (legs 0, 2, 3) doesn't tip.
    Leg 0 and 3 are diagonal (Y_DEFAULT=45, Z_DEFAULT=-50); leg 2 is forward (Y_START=0).
    Transition uses Z_UP=-30 to lift leg 1 off the ground before raising to Z_WAVE=60.
    No initial stand — already standing. No animated stand at end — explicit return.
    """
    stand_diag = [45, 45, -50]  # legs 0, 3 (Z_DEFAULT, diagonal)
    stand_fwd  = [45,  0, -50]  # leg 2 (Z_DEFAULT, forward)

    transition = [70,  0, -30]  # leg 1: lift off (X_TURN, Y_START, Z_UP)
    raised     = [ 0, 120,  60]  # leg 1: wand position (X_START, Y_WAVE, Z_WAVE)

    crawler.do_step([stand_diag, transition, stand_fwd, stand_diag], speed)
    time.sleep(0.3)

    crawler.do_step([stand_diag, raised, stand_fwd, stand_diag], speed)
    time.sleep(1.2)  # hold — spell fires here

    crawler.do_step([stand_diag, transition, stand_fwd, stand_diag], speed)
    time.sleep(0.3)

    crawler.do_step([stand_diag, stand_fwd, stand_fwd, stand_diag], speed)


def _trick_point(speed: int):
    """Extend left-front leg (index 1) forward as a point while standing, hold, return.

    Uses leg 1 (same stable side as wave) so the tripod doesn't tip.
    Transition uses Z_UP=-30 to lift off; pointed uses Z=0 (mid-height, horizontal).
    No initial stand — already standing. No animated stand at end — explicit return.
    """
    stand_diag = [45, 45, -50]  # legs 0, 3 (Z_DEFAULT, diagonal)
    stand_fwd  = [45,  0, -50]  # leg 2 (Z_DEFAULT, forward)

    transition = [70,  0, -30]  # leg 1: lift off (X_TURN, Y_START, Z_UP)
    pointed    = [ 0, 120,   0]  # leg 1: extended forward, mid-height — the point

    crawler.do_step([stand_diag, transition, stand_fwd, stand_diag], speed)
    time.sleep(0.3)

    crawler.do_step([stand_diag, pointed, stand_fwd, stand_diag], speed)
    time.sleep(2.5)

    crawler.do_step([stand_diag, transition, stand_fwd, stand_diag], speed)
    time.sleep(0.3)

    crawler.do_step([stand_diag, stand_fwd, stand_fwd, stand_diag], speed)


def _trick_wave(speed: int):
    crawler.do_action("wave", step=1, speed=speed)


_TRICKS = {
    "pushup":      _trick_pushup,
    "twist":       _trick_twist,
    "swimming":    _trick_swimming,
    "handwork":    _trick_handwork,
    "fluid_swim":  _trick_fluid_swim,
    "wand":        _trick_wand,    # internal — triggered by lumos/nox/avada_kedavra spells
    "wave":        _trick_wave,
}


@app.post("/trick")
async def trick(req: TrickRequest):
    start = time.time()
    speed = min(req.speed, MAX_TRICK_SPEED)
    logging.info(f"POST /trick  name={req.name} speed={speed}")
    if req.name not in _TRICKS:
        return _envelope("trick", {}, start, f"unknown trick: {req.name}")
    try:
        async with _motion_section():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: _TRICKS[req.name](speed))
        result = _envelope("trick", {"name": req.name}, start)
        logging.info(f"  trick ok ({result['duration_ms']}ms)")
        return result
    except Exception as e:
        logging.error(f"  trick error: {e}")
        return _envelope("trick", {"name": req.name}, start, str(e))


# ---------------------------------------------------------------------------
# MJPEG streaming
# ---------------------------------------------------------------------------

async def _mjpeg_frames():
    """Stream the shared latest JPEG. Emits Content-Length so consumers
    (laptop FrameSampler) can length-prefix-parse each part."""
    period = 1.0 / _FRAME_GRAB_HZ
    while True:
        async with _latest_frame_lock:
            frame = _latest_frame_jpeg
        if frame is not None:
            header = (
                f"--frame\r\n"
                f"Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n\r\n"
            ).encode("ascii")
            yield header + frame + b"\r\n"
        await asyncio.sleep(period)


@app.get("/stream")
async def stream():
    await camera_on()   # first viewer starts the camera
    return StreamingResponse(
        _mjpeg_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)
