"""Standalone capture viewer for the CC Chotu skill — port 8889.

Proxies the Pi's MJPEG live stream and serves the latest /tmp/chotu_capture.jpg.
Run with: python -m scripts.robot.cc_viewer
"""

import os
import pathlib

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse

PI_HOST = os.getenv("PI_HOST", "http://chotu.local:7000")
CAPTURE_PATH = pathlib.Path("/tmp/chotu_capture.jpg")

app = FastAPI()

_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Chotu — CC Viewer</title>
<style>
  body { margin: 0; background: #111; color: #eee; font-family: monospace; display: flex; flex-direction: column; align-items: center; padding: 16px; gap: 16px; }
  h2 { margin: 0; font-size: 14px; letter-spacing: 0.1em; opacity: 0.6; }
  .row { display: flex; gap: 16px; align-items: flex-start; }
  .panel { display: flex; flex-direction: column; align-items: center; gap: 6px; }
  img { border: 1px solid #333; border-radius: 4px; max-width: 480px; width: 100%; }
  #capture { cursor: pointer; }
  #ts { font-size: 11px; opacity: 0.4; }
</style>
</head>
<body>
<h2>CHOTU · CC VIEWER</h2>
<div class="row">
  <div class="panel">
    <h2>LIVE STREAM</h2>
    <img src="/stream" alt="live stream">
  </div>
  <div class="panel">
    <h2>LATEST CAPTURE</h2>
    <img id="capture" src="/capture" alt="latest capture" title="click to refresh">
    <span id="ts">—</span>
  </div>
</div>
<script>
  function refreshCapture() {
    const img = document.getElementById('capture');
    img.src = '/capture?t=' + Date.now();
    document.getElementById('ts').textContent = new Date().toLocaleTimeString();
  }
  document.getElementById('capture').addEventListener('click', refreshCapture);
  setInterval(refreshCapture, 3000);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


@app.get("/capture")
async def capture():
    if not CAPTURE_PATH.exists():
        # return a 1x1 transparent pixel placeholder
        pixel = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
            b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
            b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=8"
            b"\x837\x82<.342\x1edL\t\x13\x00\x01\x11\x00\x3f\x00\xf5\x00\x00\xff\xd9"
        )
        return Response(content=pixel, media_type="image/jpeg")
    return FileResponse(CAPTURE_PATH, media_type="image/jpeg")


@app.get("/stream")
async def stream():
    async def _pipe():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", f"{PI_HOST}/stream") as resp:
                    async for chunk in resp.aiter_bytes(4096):
                        yield chunk
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError):
            return

    return StreamingResponse(
        _pipe(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


if __name__ == "__main__":
    print(f"CC Viewer → http://localhost:8889  (Pi: {PI_HOST})")
    uvicorn.run(app, host="0.0.0.0", port=8889, log_level="warning")
