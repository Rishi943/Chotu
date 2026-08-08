"""GUI server — FastAPI on port 8888, serves the browser UI and SSE event stream."""

import asyncio
import json
import pathlib

import httpx
import uvicorn
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core import brain
from core.hearing import hear

app = FastAPI()

_STATIC = pathlib.Path(__file__).parent / "static"
_STATIC_INDEX = _STATIC / "console.html"

app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.get("/")
async def index():
    return FileResponse(_STATIC_INDEX)


async def _event_stream():
    while True:
        try:
            event = await asyncio.wait_for(brain.gui_event_queue.get(), timeout=15.0)
            yield f"data: {json.dumps(event)}\n\n"
        except asyncio.TimeoutError:
            yield "data: {\"type\":\"ping\"}\n\n"


@app.get("/events")
async def events():
    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/gallery")
async def gallery():
    return JSONResponse(brain.gallery_store)


@app.get("/stream")
async def stream():
    async def _pipe():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", f"{brain.PI_HOST}/stream") as resp:
                    async for chunk in resp.aiter_bytes(4096):
                        yield chunk
        except (httpx.ConnectError, httpx.TimeoutException):
            return
    return StreamingResponse(_pipe(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/perception")
async def perception():
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.post(f"{brain.PI_HOST}/perception", json={"face": True, "human": True})
            return JSONResponse(resp.json())
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    text = body.get("text", "")
    if text:
        brain.motion_runner.clear_stage()
        brain.pending_input.push(text)
    return JSONResponse({"ok": True})


@app.post("/stop")
async def stop():
    brain.estop.set()
    brain.motion_runner.clear_stage()
    brain.pending_input.push("[stop] freeze -- stop what you are doing")
    return JSONResponse({"ok": True})



@app.post("/audio")
async def audio(audio: UploadFile = File(...)):
    raw = await audio.read()
    if not raw:
        return JSONResponse({"ok": False, "error": "empty recording"},
                             status_code=400)
    try:
        out = await hear(raw, audio.content_type or "audio/wav")
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
    if out["text"]:
        brain.motion_runner.clear_stage()
        brain.pending_input.push(out["text"])
    return JSONResponse({"ok": True, **out})


@app.post("/thinking")
async def thinking(request: Request):
    body = await request.json()
    enabled = bool(body.get("enabled", False))
    brain.thinking_enabled = enabled
    return JSONResponse({"ok": True})


@app.post("/stt")
async def stt(request: Request):
    body = await request.json()
    enabled = bool(body.get("enabled", False))
    brain.continuous_mode = enabled
    return JSONResponse({"ok": True})


@app.post("/ptt")
async def ptt():
    if not brain.PTT_ENABLED:
        return JSONResponse({"ok": False, "error": "ptt disabled"})
    asyncio.create_task(brain.trigger_ptt_capture())
    return JSONResponse({"ok": True})


@app.post("/handsfree")
async def handsfree(request: Request):
    if not brain.PTT_ENABLED:
        return JSONResponse({"ok": False, "error": "ptt disabled"})
    body = await request.json()
    brain.set_handsfree(bool(body.get("enabled", False)))
    return JSONResponse({"ok": True})


@app.get("/api/config")
async def config():
    return JSONResponse({
        "ptt_enabled": brain.PTT_ENABLED,
        "model": getattr(brain.llm_client, "model", ""),
        "bridge": brain.PI_HOST,
    })


@app.get("/api/battery")
async def battery():
    async with httpx.AsyncClient(timeout=4.0) as client:
        try:
            resp = await client.get(f"{brain.PI_HOST}/battery")
            return JSONResponse(resp.json())
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


_CERTS = pathlib.Path(__file__).resolve().parents[1] / "certs"


async def run_gui_server():
    cert, key = _CERTS / "chotu.pem", _CERTS / "chotu-key.pem"
    kwargs = {}
    if cert.exists() and key.exists():
        kwargs = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    else:
        print("[gui] no cert -- serving plain HTTP. The phone mic will NOT work. "
              "Run scripts/make_cert.py")
    config = uvicorn.Config(app, host="0.0.0.0", port=8888,
                             log_level="warning", **kwargs)
    server = uvicorn.Server(config)
    await server.serve()
