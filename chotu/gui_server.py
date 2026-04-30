"""GUI server — FastAPI on port 8888, serves the browser UI and SSE event stream."""

import asyncio
import json
import pathlib

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from chotu import brain

app = FastAPI()

_STATIC_INDEX = pathlib.Path(__file__).parent / "static" / "index.html"


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
        brain.input_queue.put_nowait(text)
    return JSONResponse({"ok": True})


@app.post("/mode")
async def mode(request: Request):
    body = await request.json()
    mode_str = body.get("mode", "reactive")
    goal_text = body.get("goal_text")
    await brain.set_mode(mode_str, goal_text)
    return JSONResponse({"ok": True})


@app.post("/thinking")
async def thinking(request: Request):
    body = await request.json()
    enabled = bool(body.get("enabled", False))
    brain.thinking_enabled = enabled
    return JSONResponse({"ok": True})


async def run_gui_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=8888, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
