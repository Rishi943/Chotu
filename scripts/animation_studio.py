"""Chotu Animation Studio launcher + Pi proxy.

Serves studio.html and forwards the few motion endpoints the browser needs to
the Pi bridge (the browser can't POST cross-origin to the Pi). Independent of
core.brain. Run: python -m scripts.animation_studio  (then open :8899).
"""

import os
import pathlib

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

load_dotenv()
PI_HOST = os.getenv("PI_HOST", "http://chotu.local:7000")
_STUDIO_HTML = pathlib.Path(__file__).parent / "studio.html"

app = FastAPI()
_client = httpx.AsyncClient(timeout=30.0)  # set_legs/pose can take many seconds


async def _forward(method: str, path: str, json: dict | None = None):
    try:
        r = await _client.request(method, f"{PI_HOST}{path}", json=json)
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"pi_unreachable: {e}"}, status_code=502
        )


@app.get("/")
async def index():
    return FileResponse(_STUDIO_HTML)


@app.get("/health")
async def health():
    return await _forward("GET", "/health")


@app.post("/set_legs")
async def set_legs(req: Request):
    body = await req.json()
    return await _forward(
        "POST", "/set_legs", {"legs": body["legs"], "speed": body.get("speed", 60)}
    )


@app.post("/pose")
async def pose(req: Request):
    body = await req.json()
    return await _forward(
        "POST", "/pose", {"name": body["name"], "speed": body.get("speed", 40)}
    )


def main():
    print(f"Chotu Animation Studio: http://localhost:8899  (Pi: {PI_HOST})")
    uvicorn.run(app, host="0.0.0.0", port=8899, log_level="warning")


if __name__ == "__main__":
    main()
