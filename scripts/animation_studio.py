"""Chotu Animation Studio launcher + Pi proxy.

Serves studio.html and forwards the few motion endpoints the browser needs to
the Pi bridge (the browser can't POST cross-origin to the Pi). Independent of
core.brain. Run: python -m scripts.animation_studio  (then open :8899).
"""

import json
import os
import pathlib
import re

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

load_dotenv()
PI_HOST = os.getenv("PI_HOST", "http://chotu.local:7000")
_STUDIO_HTML = pathlib.Path(__file__).parent / "studio.html"

_REPO = pathlib.Path(__file__).resolve().parent.parent
_ANIM_DIR = _REPO / "assets" / "Animations"
_BUILTIN_DIR = _ANIM_DIR / "builtin"
_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
_ANIM_DIR.mkdir(parents=True, exist_ok=True)
_BUILTIN_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()
_client = httpx.AsyncClient(timeout=30.0)  # set_legs/pose can take many seconds


def _read_anim(path: pathlib.Path, builtin: bool):
    try:
        d = json.loads(path.read_text())
    except Exception as e:
        print(f"skipping invalid animation {path.name}: {e}")
        return None
    return {
        "file": path.name, "name": d.get("tool") or path.stem, "builtin": builtin,
        "tool": d.get("tool") or path.stem, "description": d.get("description", ""),
        "persona_gated": bool(d.get("persona_gated", False)),
        "default_speed": d.get("default_speed", 60), "frames": d.get("frames", []),
    }


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


@app.get("/animations")
async def list_animations():
    out = []
    for p in sorted(_ANIM_DIR.glob("*.json")):
        a = _read_anim(p, False)
        if a:
            out.append(a)
    for p in sorted(_BUILTIN_DIR.glob("*.json")):
        a = _read_anim(p, True)
        if a:
            out.append(a)
    return {"animations": out}


@app.post("/animations")
async def save_animation(req: Request):
    d = await req.json()
    tool = (d.get("tool") or "").strip()
    if not _SNAKE.match(tool) or not d.get("frames"):
        return JSONResponse(
            {"ok": False, "error": "tool must be snake_case and have >=1 frame"},
            status_code=400,
        )
    dest = (_ANIM_DIR / f"{tool}.json").resolve()
    if dest.parent != _ANIM_DIR.resolve():
        return JSONResponse({"ok": False, "error": "invalid path"}, status_code=403)
    dest.write_text(json.dumps(d, indent=2))
    return {"ok": True, "file": dest.name}


def main():
    print(f"Chotu Animation Studio: http://localhost:8899  (Pi: {PI_HOST})")
    uvicorn.run(app, host="0.0.0.0", port=8899, log_level="warning")


if __name__ == "__main__":
    main()
