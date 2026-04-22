#!/usr/bin/env python3
"""Chotu Pi bridge — dumb FastAPI server wrapping PiCrawler hardware.

Run with: sudo ~/chotu-bridge/.venv/bin/python3 server.py
Requires sudo for GPIO / robot_hat access.
"""

import asyncio
import base64
import logging
import subprocess
import time
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

import cv2
import robot_hat
from fastapi import FastAPI
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
    Vilib.camera_stop()


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
    speed: int = 50


class PoseRequest(BaseModel):
    name: str        # "stand" | "sit" | "wave" | "push up" | "look up" | "look down" | "look left" | "look right"


class SpeakRequest(BaseModel):
    text: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    start = time.time()
    logging.info("GET /health")
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
    logging.info(f"POST /pose  name={req.name}")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: crawler.do_action(req.name))
        held_ms = int((time.time() - start) * 1000)
        result = _envelope("pose", {"pose": req.name, "held_ms": held_ms}, start)
        logging.info(f"  pose ok ({held_ms}ms)")
        return result
    except Exception as e:
        logging.error(f"  pose error: {e}")
        return _envelope("pose", {"pose": req.name, "held_ms": 0}, start, str(e))


@app.post("/speak")
async def speak(req: SpeakRequest):
    start = time.time()
    logging.info(f"POST /speak  text={req.text!r}")
    try:
        subprocess.run(
            ["espeak", "-v", "en", req.text],
            check=True,
            timeout=30,
            capture_output=True,
        )
        result = _envelope("speak", {"text": req.text, "played": True}, start)
        logging.info(f"  speak ok ({result['duration_ms']}ms)")
        return result
    except Exception as e:
        logging.error(f"  speak error: {e}")
        return _envelope("speak", {"text": req.text, "played": False}, start, str(e))


@app.get("/distance")
async def distance():
    start = time.time()
    logging.info("GET /distance")
    try:
        cm = float(us.read())
        reliable = 2.0 <= cm <= 300.0
        result = _envelope("get_distance", {"cm": round(cm, 1), "reliable": reliable}, start)
        logging.info(f"  distance={cm}cm reliable={reliable}")
        return result
    except Exception as e:
        logging.error(f"  distance error: {e}")
        return _envelope("get_distance", {"cm": 0, "reliable": False}, start, str(e))


@app.post("/capture")
async def capture():
    start = time.time()
    logging.info("POST /capture")
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
    logging.info("GET /battery")
    try:
        voltage = _read_battery_voltage()
        percent = _voltage_to_percent(voltage)
        result = _envelope("battery", {"voltage": voltage, "percent": percent, "charging": False}, start)
        logging.info(f"  battery {voltage}V {percent}%")
        return result
    except Exception as e:
        logging.error(f"  battery error: {e}")
        return _envelope("battery", {}, start, str(e))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)
