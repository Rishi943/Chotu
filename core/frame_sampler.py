"""FrameSampler — connects to Pi /stream, parses MJPEG, keeps deque of recent
JPEG frames, and pushes throttled frames to the active backend.

The Pi /stream emits ~10 FPS. The sampler throttles backend pushes to
`sample_hz` (default 1.0). The buffer always sees every parsed frame so
`latest()` is fresh for `capture_vision`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Optional, Protocol

import httpx


log = logging.getLogger(__name__)


class _FrameTarget(Protocol):
    async def send_frame(self, jpeg_bytes: bytes, ts: float) -> None: ...


class FrameSampler:
    def __init__(
        self,
        *,
        backend: Optional[_FrameTarget],
        stream_url: str,
        buffer_size: int = 3,
        sample_hz: float = 1.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.backend = backend
        self.stream_url = stream_url
        self.sample_period = 1.0 / sample_hz if sample_hz > 0 else 0.0
        self._buffer: deque[bytes] = deque(maxlen=buffer_size)
        self._client = client
        self._task: Optional[asyncio.Task] = None
        self._last_push_ts: float = 0.0
        self._stopped = asyncio.Event()

    def latest(self) -> Optional[bytes]:
        return self._buffer[-1] if self._buffer else None

    def all_buffered(self) -> list[bytes]:
        return list(self._buffer)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="FrameSampler")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self) -> None:
        client = self._client or httpx.AsyncClient(timeout=None)
        owns_client = self._client is None
        try:
            while not self._stopped.is_set():
                try:
                    async with client.stream("GET", self.stream_url) as resp:
                        resp.raise_for_status()
                        async for frame in self._iter_mjpeg(resp):
                            await self._on_frame(frame)
                            if self._stopped.is_set():
                                break
                except asyncio.CancelledError:
                    raise
                except httpx.HTTPError as e:
                    log.warning("FrameSampler reconnecting after error: %s", e)
                    await asyncio.sleep(1.0)
        finally:
            if owns_client:
                await client.aclose()

    async def _iter_mjpeg(self, resp):
        """Parse a multipart/x-mixed-replace MJPEG stream into JPEG byte blobs.
        Requires the Pi to emit Content-Length per part."""
        boundary = b"--frame"
        buf = b""
        async for chunk in resp.aiter_bytes():
            buf += chunk
            while True:
                hdr_end = buf.find(b"\r\n\r\n")
                if hdr_end < 0:
                    break
                header = buf[:hdr_end].decode("ascii", errors="ignore")
                clen = 0
                for line in header.splitlines():
                    if line.lower().startswith("content-length:"):
                        try:
                            clen = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            clen = 0
                body_start = hdr_end + 4
                if clen <= 0 or len(buf) < body_start + clen:
                    break
                yield buf[body_start : body_start + clen]
                buf = buf[body_start + clen + 2 :]
                if buf.startswith(boundary):
                    nl = buf.find(b"\r\n")
                    if nl >= 0:
                        buf = buf[nl + 2 :]

    async def _on_frame(self, jpeg: bytes) -> None:
        self._buffer.append(jpeg)
        now = time.monotonic()
        if self.backend is not None and (now - self._last_push_ts) >= self.sample_period:
            self._last_push_ts = now
            try:
                await self.backend.send_frame(jpeg, now)
            except Exception as e:
                log.warning("backend.send_frame failed: %s", e)
