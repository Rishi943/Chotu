#!/usr/bin/env python3
"""Chotu Pi bridge — dumb FastAPI server wrapping PiCrawler hardware.

Run with: sudo ~/chotu-bridge/.venv/bin/python3 server.py
Requires sudo for GPIO / robot_hat access.
"""

import asyncio
import base64
import logging
import os
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Suppress uvicorn access-log noise for high-frequency poll endpoints.
class _PollFilter(logging.Filter):
    _MUTED = {"/distance", "/health", "/battery"}
    def filter(self, record):
        msg = record.getMessage()
        return not any(p in msg for p in self._MUTED)

logging.getLogger("uvicorn.access").addFilter(_PollFilter())

import cv2
import pygame
import robot_hat
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from picrawler import Picrawler
from robot_hat import Ultrasonic, Pin, ADC
from vilib import Vilib

# ---------------------------------------------------------------------------
# Hardware init — reset_mcu() must come first, before any robot_hat use
# ---------------------------------------------------------------------------

robot_hat.reset_mcu()
time.sleep(0.2)

crawler = Picrawler()
us = Ultrasonic(Pin("D2"), Pin("D3"))
_bat_adc = ADC("A4")
pygame.mixer.init()  # must run before speak uses pygame.mixer.Sound


def _read_battery_voltage() -> float:
    """Read battery via ADC A4 with 3× voltage divider (robot_hat standard)."""
    return round(_bat_adc.read_voltage() * 3, 2)


def _voltage_to_percent(v: float) -> int:
    """Map 2S LiPo range (6.0–8.4 V) to 0–100%."""
    return max(0, min(100, int((v - 6.0) / (8.4 - 6.0) * 100)))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    Vilib.camera_start(vflip=False, hflip=False)
    await asyncio.sleep(1)  # camera warm-up
    yield
    Vilib.camera_close()


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
    speed: int = 80


class PoseRequest(BaseModel):
    name: str        # "stand" | "sit" | "wave" | "push up" | "look up" | "look down" | "look left" | "look right"
    speed: int = 80


class SpeakRequest(BaseModel):
    text: str


class SetLegsRequest(BaseModel):
    legs: list[list[float]]  # 4 × [x, y, z] in mm
    speed: int = 80


class TrickRequest(BaseModel):
    name: str        # "pushup" | "twist" | "swimming" | "handwork"
    speed: int = 80


VILIB_COLORS = {"red", "orange", "yellow", "green", "blue", "purple"}


class PerceptionRequest(BaseModel):
    color: str | None = None
    face: bool = False
    human: bool = False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    start = time.time()
    return _envelope("health", {"status": "ok"}, start)


@app.post("/move")
async def move(req: MoveRequest):
    start = time.time()
    logging.info(f"POST /move  direction={req.direction} steps={req.steps} speed={req.speed}")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: crawler.do_action(req.direction, req.steps, req.speed),
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
    logging.info(f"POST /pose  name={req.name} speed={req.speed}")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: crawler.do_step(req.name, req.speed))
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
    logging.info(f"POST /set_legs  legs={req.legs} speed={req.speed}")
    try:
        if len(req.legs) != 4 or any(len(leg) != 3 for leg in req.legs):
            raise ValueError("legs must be 4 × [x, y, z]")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: crawler.do_step(req.legs, req.speed),
        )
        result = _envelope("set_legs", {"legs": req.legs, "speed": req.speed}, start)
        logging.info(f"  set_legs ok ({result['duration_ms']}ms)")
        return result
    except Exception as e:
        logging.error(f"  set_legs error: {e}")
        return _envelope("set_legs", {"legs": req.legs, "speed": req.speed}, start, str(e))


@app.post("/speak")
async def speak(req: SpeakRequest):
    start = time.time()
    logging.info(f"POST /speak  text={req.text!r}")
    try:
        loop = asyncio.get_event_loop()

        def _do_speak():
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmpfile = f.name
            try:
                subprocess.run(["espeak", "-w", tmpfile, "-v", "en", req.text], check=True, timeout=30)
                channel = pygame.mixer.Sound(tmpfile).play()
                while channel.get_busy():
                    time.sleep(0.05)
            finally:
                try:
                    os.unlink(tmpfile)
                except OSError:
                    pass

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


@app.post("/capture")
async def capture():
    start = time.time()
    try:
        frame = Vilib.img
        if frame is None:
            logging.warning("  capture: no frame available")
            return _envelope("capture", {}, start, "no frame available from camera")
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        image_b64 = base64.b64encode(buf).decode()
        kb = len(image_b64) * 3 // 4 // 1024
        logging.info(f"  capture ok (~{kb}KB, {int((time.time()-start)*1000)}ms)")
        return _envelope("capture", {"image_base64": image_b64}, start)
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
    crawler.do_step(up, speed);   time.sleep(0.6)
    crawler.do_step(down, speed); time.sleep(0.6)


def _trick_twist(speed: int):
    new_step = [[50, 50, -80], [50, 50, -80], [50, 50, -80], [50, 50, -80]]
    for i in range(4):
        for inc in range(30, 60, 5):
            rise = [50, 50, -80 + inc * 0.5]
            drop = [50, 50, -80 - inc]
            new_step[i]           = rise
            new_step[(i + 2) % 4] = drop
            new_step[(i + 1) % 4] = rise
            new_step[(i - 1) % 4] = drop
            crawler.do_step(new_step, speed)
            time.sleep(0.02)


def _trick_swimming(speed: int, loops: int = 40):
    for i in range(loops):
        crawler.do_step(
            [
                [100 - i, i, 0],
                [100 - i, i, 0],
                [0, 120, -60 + i / 5],
                [0, 100, -40 - i / 5],
            ],
            speed,
        )
        time.sleep(0.01)


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


_TRICKS = {
    "pushup":   _trick_pushup,
    "twist":    _trick_twist,
    "swimming": _trick_swimming,
    "handwork": _trick_handwork,
}


@app.post("/trick")
async def trick(req: TrickRequest):
    start = time.time()
    logging.info(f"POST /trick  name={req.name} speed={req.speed}")
    if req.name not in _TRICKS:
        return _envelope("trick", {}, start, f"unknown trick: {req.name}")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _TRICKS[req.name](req.speed))
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
    while True:
        frame = Vilib.img
        if frame is not None:
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                    + buf.tobytes()
                    + b"\r\n"
                )
        await asyncio.sleep(0.05)


@app.get("/stream")
async def stream():
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
