# Live Brain v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a persistent, async, vision-grounded Chotu brain backed by Gemini 3.1 Flash Live Preview, running alongside the existing stateless llama-server flow as a sibling backend selected at startup.

**Architecture:** A `Backend` async protocol abstracts the LLM. `LlamaServerBackend` wraps the existing turn-based call. `GeminiLiveBackend` keeps a WebSocket open, pushes 1 FPS frames continuously, and emits typed events. `brain.py` becomes a producer/consumer pair owning the frame buffer, motion lock, and transcript log. The Pi grows a `/stream` MJPEG endpoint shared by the laptop sampler.

**Tech Stack:** Python 3.12, asyncio. `google-genai` SDK for Gemini Live (websocket-based, official). `httpx` for the Pi MJPEG sampler. `pytest` + `pytest-asyncio` for new unit tests. Existing OpenAI + Anthropic SDKs untouched.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `core/backend.py` | NEW | `Backend` async Protocol, `Event` dataclasses |
| `core/llama_backend.py` | NEW | Wraps `LLMClient` into the `Backend` shape (stateless mode) |
| `core/gemini_live_backend.py` | NEW | Gemini Live WebSocket adapter (live mode) |
| `core/motion_lock.py` | NEW | Single async lock + metadata + reject helper |
| `core/frame_sampler.py` | NEW | MJPEG sampler, owns `deque(maxlen=3)`, pushes to active backend |
| `core/brain.py` | MODIFY | Producer/consumer refactor, replay-ready transcript log, wake nudge, backend selection |
| `core/tools.py` | MODIFY | Motion-tool wrappers consult `MotionLock`; `capture_vision` reads from sampler buffer |
| `core/prompts.py` | MODIFY | Persona file selection per active backend |
| `core/events.py` | MODIFY | `inject_event` also targets active backend in live mode |
| `pi_bridge/server.py` | MODIFY | New `/stream` MJPEG endpoint; `/capture` reads latest streamed frame |
| `CHOTU.md` | DELETE | Replaced by split below (after migration commit) |
| `CHOTU_BASE.md` | NEW | Shared voice/personality/physical constraints |
| `CHOTU_STATELESS.md` | NEW | Heartbeat-rhythm rules (extracted from current `CHOTU.md`) |
| `CHOTU_LIVE.md` | NEW | Continuous-reactivity rules, motion-rejection guidance |
| `.env.example` | MODIFY | `PALIV_BRAIN_MODE`, `GEMINI_API_KEY` |
| `pyproject.toml` / `requirements.txt` | MODIFY | Add `google-genai`, `pytest-asyncio` |
| `tests/test_motion_lock.py` | NEW | Unit tests for the lock |
| `tests/test_backend_protocol.py` | NEW | Sanity tests for `Backend` event dataclasses |
| `tests/test_frame_sampler.py` | NEW | Unit tests with mocked HTTP stream |
| `tests/test_llama_backend.py` | NEW | Adapter shape tests against a fake `LLMClient` |
| `tests/conftest.py` | NEW | pytest-asyncio mode setting |

---

## Task 1: Project scaffolding for tests

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add test deps**

Append to `requirements.txt`:

```
pytest>=8.0
pytest-asyncio>=0.23
```

- [ ] **Step 2: Create tests package**

```bash
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 3: Set pytest-asyncio mode**

Write `tests/conftest.py`:

```python
"""pytest-asyncio in auto mode (set in pyproject.toml) handles async tests
without per-function decoration. This file exists so pytest treats tests/ as
a rooted package for imports."""
```

Add to `pyproject.toml` (create the file at repo root if it doesn't exist) under `[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 4: Install and verify**

```bash
source .venv/bin/activate
pip install -r requirements.txt
pytest --collect-only
```

Expected: collects 0 tests cleanly, no import errors.

- [ ] **Step 5: Commit**

```bash
git add tests/ requirements.txt pyproject.toml
git commit -m "test(infra): add pytest + pytest-asyncio scaffolding for live-brain work"
```

---

## Task 2: Backend protocol and event types

**Files:**
- Create: `core/backend.py`
- Create: `tests/test_backend_protocol.py`

- [ ] **Step 1: Write the failing test**

Write `tests/test_backend_protocol.py`:

```python
"""Sanity tests for the Backend Event dataclasses."""
from core.backend import ToolCall, AssistantText, SessionEnded, BackendError, Event


def test_toolcall_fields():
    tc = ToolCall(id="fc-1", name="speak", args={"text": "hi"})
    assert tc.id == "fc-1"
    assert tc.name == "speak"
    assert tc.args == {"text": "hi"}


def test_assistant_text_fields():
    at = AssistantText(text="kitchen on it")
    assert at.text == "kitchen on it"


def test_session_ended_is_event():
    assert isinstance(SessionEnded(reason="goaway"), Event)


def test_backend_error_carries_message():
    e = BackendError(message="ws closed", recoverable=False)
    assert "ws closed" in e.message
    assert e.recoverable is False
```

- [ ] **Step 2: Run test, verify it fails**

```bash
pytest tests/test_backend_protocol.py -v
```

Expected: `ImportError: cannot import name 'ToolCall' from 'core.backend'`.

- [ ] **Step 3: Implement `core/backend.py`**

```python
"""Backend protocol and event types for the live-brain pivot.

A Backend abstracts the LLM transport. Stateless mode uses LlamaServerBackend
(one request/response per turn), live mode uses GeminiLiveBackend (persistent
WebSocket, frames pushed continuously). The brain loop runs two tasks:
a producer that feeds the backend (text + frames + tool results) and a
consumer that drains backend.events() and dispatches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, Union


# --- Event types ---

@dataclass
class ToolCall:
    """Model wants to invoke a tool. The brain dispatches and replies via send_tool_result."""
    id: str
    name: str
    args: dict


@dataclass
class AssistantText:
    """Inner-monologue text from the model. Shown in transcript, not spoken aloud
    (speech is a separate `speak` tool call)."""
    text: str


@dataclass
class SessionEnded:
    """Backend's session has closed cleanly. Reason is human-readable."""
    reason: str


@dataclass
class BackendError:
    """Backend raised an unrecoverable error. The brain loop should stop in v1."""
    message: str
    recoverable: bool = False


Event = Union[ToolCall, AssistantText, SessionEnded, BackendError]


# --- Backend protocol ---

class Backend(Protocol):
    """All LLM backends implement this shape. Async-event-streaming by design;
    stateless backends adapt up to it."""

    async def start(self) -> None:
        """Open whatever connection is needed. May be a no-op for stateless backends."""
        ...

    async def send_user_text(self, text: str) -> None:
        """Push a user-role text turn into the model's context."""
        ...

    async def send_frame(self, jpeg_bytes: bytes, ts: float) -> None:
        """Push one JPEG frame. ts is the laptop monotonic timestamp at capture."""
        ...

    async def send_tool_result(self, tool_call_id: str, result: dict) -> None:
        """Reply to a ToolCall the model previously emitted. result is the Pi envelope."""
        ...

    async def events(self) -> AsyncIterator[Event]:
        """Stream events from the model. Brain's consumer task drains this."""
        ...

    async def close(self) -> None:
        """Tear down. Idempotent."""
        ...
```

- [ ] **Step 4: Run test, verify pass**

```bash
pytest tests/test_backend_protocol.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add core/backend.py tests/test_backend_protocol.py
git commit -m "feat(backend): add Backend protocol and Event dataclasses"
```

---

## Task 3: MotionLock — pure logic, full TDD

**Files:**
- Create: `core/motion_lock.py`
- Create: `tests/test_motion_lock.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/test_motion_lock.py`:

```python
"""MotionLock: at most one motion tool in flight at a time. New attempts while
held are REJECTED (not queued) with an informative envelope so the model sees
it and replans."""

import asyncio
import time

import pytest

from core.motion_lock import MotionLock, REJECTED_ENVELOPE_KEYS


async def test_acquire_when_free():
    lock = MotionLock()
    async with lock.acquire("move", {"direction": "forward"}, eta_ms=3000) as ok:
        assert ok is True


async def test_reject_when_held_returns_envelope():
    lock = MotionLock()
    async with lock.acquire("trick", {"name": "pushup"}, eta_ms=6000):
        rejection = lock.try_acquire("move", {"direction": "forward"}, eta_ms=3000)
        assert rejection is not None
        # rejection should be a dict shaped like a Pi error envelope
        for key in REJECTED_ENVELOPE_KEYS:
            assert key in rejection
        assert rejection["ok"] is False
        assert "motion in progress" in rejection["error"]
        assert "trick" in rejection["error"]


async def test_released_after_context_exit():
    lock = MotionLock()
    async with lock.acquire("move", {}, eta_ms=100):
        pass
    # Should be free again
    rejection = lock.try_acquire("turn", {}, eta_ms=100)
    assert rejection is None  # None means free, caller may acquire


async def test_metadata_reports_active_tool():
    lock = MotionLock()
    assert lock.active is None
    async with lock.acquire("trick", {"name": "wave"}, eta_ms=4000):
        active = lock.active
        assert active is not None
        assert active["tool"] == "trick"
        assert active["args"] == {"name": "wave"}
        assert active["eta_ms"] == 4000
        assert isinstance(active["started_at"], float)
    assert lock.active is None


async def test_remaining_ms_decreases():
    lock = MotionLock()
    async with lock.acquire("move", {}, eta_ms=200):
        first = lock.remaining_ms()
        await asyncio.sleep(0.05)
        second = lock.remaining_ms()
        assert second < first
```

- [ ] **Step 2: Run, verify fails**

```bash
pytest tests/test_motion_lock.py -v
```

Expected: ImportError on `core.motion_lock`.

- [ ] **Step 3: Implement `core/motion_lock.py`**

```python
"""Single-motion-at-a-time lock.

Motion tools: move, turn, set_legs, pose, trick. Only one runs at a time.
Attempts to start a second motion while one is held are REJECTED — never
queued — with a dict envelope shaped like a Pi error response. The model
sees the rejection in its tool-result stream and can replan in-context.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Optional


MOTION_TOOLS = frozenset({"move", "turn", "set_legs", "pose", "trick"})

# Keys the rejection envelope is guaranteed to contain (used by tests + dispatch).
REJECTED_ENVELOPE_KEYS = ("ok", "tool", "result", "duration_ms", "timestamp", "error")


class MotionLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active: Optional[dict] = None

    @property
    def active(self) -> Optional[dict]:
        """Currently-running motion metadata, or None when free."""
        return dict(self._active) if self._active else None

    def remaining_ms(self) -> int:
        """Estimated milliseconds remaining on the active motion. 0 when free."""
        if not self._active:
            return 0
        elapsed = (time.monotonic() - self._active["started_at"]) * 1000.0
        return max(0, int(self._active["eta_ms"] - elapsed))

    def try_acquire(self, tool: str, args: dict, eta_ms: int) -> Optional[dict]:
        """Non-blocking probe. Returns None if caller may proceed, or a rejection
        envelope dict to return to the model. Does NOT actually acquire the lock —
        use `acquire()` for that."""
        if self._lock.locked():
            return self._rejection_envelope(tool)
        return None

    @asynccontextmanager
    async def acquire(self, tool: str, args: dict, eta_ms: int):
        """Async context manager. Yields True on acquire, False if already held
        (the caller should fall back to the rejection envelope from try_acquire)."""
        if self._lock.locked():
            yield False
            return
        await self._lock.acquire()
        self._active = {
            "tool": tool,
            "args": dict(args),
            "started_at": time.monotonic(),
            "eta_ms": int(eta_ms),
        }
        try:
            yield True
        finally:
            self._active = None
            self._lock.release()

    def _rejection_envelope(self, attempted_tool: str) -> dict:
        a = self._active or {}
        remaining_s = self.remaining_ms() / 1000.0
        active_tool = a.get("tool", "?")
        active_args = a.get("args", {})
        arg_hint = ""
        if active_tool == "trick" and "name" in active_args:
            arg_hint = f"({active_args['name']})"
        elif active_tool == "move" and "direction" in active_args:
            arg_hint = f"({active_args['direction']})"
        return {
            "ok": False,
            "tool": attempted_tool,
            "result": {},
            "duration_ms": 0,
            "timestamp": time.time(),
            "error": f"motion in progress: {active_tool}{arg_hint}, ~{remaining_s:.1f}s remaining",
        }
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/test_motion_lock.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add core/motion_lock.py tests/test_motion_lock.py
git commit -m "feat(motion-lock): single-motion-at-a-time async lock with reject-with-envelope"
```

---

## Task 4: Pi `/stream` MJPEG endpoint and `/capture` repurpose

**Files:**
- Modify: `pi_bridge/server.py`

- [ ] **Step 1: Locate the existing camera section**

Read `pi_bridge/server.py` and find the `/capture` route plus the `lifespan` function that calls `Vilib.camera_start(...)`. The camera is started once in `lifespan`; `/capture` currently grabs a frame on demand. We need to: (a) keep a shared "latest frame" reference in memory, (b) add `/stream` that yields MJPEG forever, (c) repurpose `/capture` to return the latest in-memory frame as base64 JSON (same response shape as before).

- [ ] **Step 2: Add a shared latest-frame buffer**

Near the other module globals (after `crawler = Picrawler()`), add:

```python
# --- Latest camera frame (shared between /stream and /capture) ---
_latest_frame_jpeg: bytes | None = None
_latest_frame_lock = asyncio.Lock()
_frame_grab_interval = 1.0 / 10  # ~10 FPS internal sampling; consumers downsample
```

- [ ] **Step 3: Add a background frame-grabber task in lifespan**

In `lifespan`, after the existing AWB block, before `yield`, add:

```python
async def _grab_loop():
    global _latest_frame_jpeg
    while True:
        try:
            frame = Vilib.img.copy() if Vilib.img is not None else None
            if frame is not None:
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    async with _latest_frame_lock:
                        _latest_frame_jpeg = buf.tobytes()
        except Exception as e:
            logging.warning(f"frame grab error: {e}")
        await asyncio.sleep(_frame_grab_interval)

grab_task = asyncio.create_task(_grab_loop())
try:
    yield
finally:
    grab_task.cancel()
```

Replace the existing `yield` line with this block.

- [ ] **Step 4: Add `/stream` MJPEG endpoint**

Add a new route (any place after `app = FastAPI(...)`):

```python
@app.get("/stream")
async def stream_mjpeg():
    """MJPEG multipart stream. Consumers (laptop FrameSampler) read chunks
    and decode each as a JPEG. Stream runs forever; ~10 FPS source."""

    boundary = "frame"

    async def gen():
        while True:
            async with _latest_frame_lock:
                frame = _latest_frame_jpeg
            if frame is not None:
                yield (
                    f"--{boundary}\r\n"
                    f"Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame)}\r\n\r\n"
                ).encode("ascii") + frame + b"\r\n"
            await asyncio.sleep(_frame_grab_interval)

    return StreamingResponse(
        gen(),
        media_type=f"multipart/x-mixed-replace; boundary={boundary}",
    )
```

- [ ] **Step 5: Repurpose `/capture` to read latest buffered frame**

Find the existing `/capture` route. Replace its body so it reads the shared frame instead of grabbing one fresh:

```python
@app.get("/capture")
async def capture():
    started = time.time()
    async with _latest_frame_lock:
        frame = _latest_frame_jpeg
    if frame is None:
        return {
            "ok": False, "tool": "capture", "result": {}, "duration_ms": 0,
            "timestamp": started, "error": "no frame available yet",
        }
    b64 = base64.b64encode(frame).decode("ascii")
    return {
        "ok": True, "tool": "capture",
        "result": {"image_base64": b64},
        "duration_ms": int((time.time() - started) * 1000),
        "timestamp": started, "error": None,
    }
```

- [ ] **Step 6: Suppress `/stream` from the access log filter**

In the `_PollFilter._MUTED` set near the top of the file, add `"/stream"`:

```python
_MUTED = {"/distance", "/health", "/battery", "/stream"}
```

- [ ] **Step 7: Deploy and verify on the Pi**

From the laptop:

```bash
scp pi_bridge/server.py chotu@chotu.local:~/chotu-bridge/server.py
ssh chotu@chotu.local 'sudo systemctl restart chotu-bridge'  # or restart manually per CLAUDE.md
```

Verify the stream from the laptop:

```bash
curl -s --max-time 3 http://chotu.local:7000/stream > /tmp/stream.bin
ls -la /tmp/stream.bin   # > 50 KB after 3s means frames are flowing
curl -s http://chotu.local:7000/capture | jq '.ok, .result.image_base64 | length'
```

Expected: `/stream` produces a non-empty multipart blob; `/capture` returns `ok: true` with a base64 string of length > 5000.

- [ ] **Step 8: Commit**

```bash
git add pi_bridge/server.py
git commit -m "feat(pi): add /stream MJPEG endpoint; /capture reads shared frame buffer"
```

---

## Task 5: FrameSampler — laptop-side 1 FPS sampler with deque

**Files:**
- Create: `core/frame_sampler.py`
- Create: `tests/test_frame_sampler.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/test_frame_sampler.py`:

```python
"""FrameSampler: connects to Pi /stream, decodes MJPEG, keeps deque of last N
JPEGs, pushes each new frame to the active backend. Sampling rate ~1 FPS."""

import asyncio
from collections import deque
from unittest.mock import AsyncMock

import pytest

from core.frame_sampler import FrameSampler


class FakeBackend:
    def __init__(self):
        self.frames: list[tuple[bytes, float]] = []

    async def send_frame(self, jpeg_bytes: bytes, ts: float) -> None:
        self.frames.append((jpeg_bytes, ts))


async def test_latest_returns_none_when_empty():
    backend = FakeBackend()
    s = FrameSampler(backend=backend, stream_url="http://x/stream", buffer_size=3, sample_hz=1.0)
    assert s.latest() is None


async def test_buffer_caps_at_size():
    backend = FakeBackend()
    s = FrameSampler(backend=backend, stream_url="http://x/stream", buffer_size=3, sample_hz=1.0)
    # Drive the buffer directly to test the cap (no I/O)
    for i in range(5):
        await s._on_frame(f"frame{i}".encode())
    buf = list(s._buffer)
    assert len(buf) == 3
    assert buf[-1] == b"frame4"
    assert buf[0] == b"frame2"


async def test_frame_pushed_to_backend():
    backend = FakeBackend()
    s = FrameSampler(backend=backend, stream_url="http://x/stream", buffer_size=3, sample_hz=1.0)
    await s._on_frame(b"hello")
    assert len(backend.frames) == 1
    assert backend.frames[0][0] == b"hello"
    assert isinstance(backend.frames[0][1], float)


async def test_sample_rate_throttles_backend_pushes():
    """Internal stream may be 10 FPS but backend only gets 1 FPS."""
    backend = FakeBackend()
    s = FrameSampler(backend=backend, stream_url="http://x/stream", buffer_size=3, sample_hz=2.0)
    # Push 5 frames back-to-back; only ~1 should reach backend due to throttle
    for i in range(5):
        await s._on_frame(f"f{i}".encode())
    # Buffer always gets every frame; backend only gets throttled ones
    assert len(s._buffer) == 3
    # First frame always passes, subsequent within 1/sample_hz s do not
    assert len(backend.frames) == 1
```

- [ ] **Step 2: Run, verify fails**

```bash
pytest tests/test_frame_sampler.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `core/frame_sampler.py`**

```python
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
                except (httpx.HTTPError, asyncio.CancelledError) as e:
                    if isinstance(e, asyncio.CancelledError):
                        raise
                    log.warning("FrameSampler reconnecting after error: %s", e)
                    await asyncio.sleep(1.0)
        finally:
            if owns_client:
                await client.aclose()

    async def _iter_mjpeg(self, resp):
        """Parse a multipart/x-mixed-replace MJPEG stream into JPEG byte blobs."""
        boundary = b"--frame"
        buf = b""
        async for chunk in resp.aiter_bytes():
            buf += chunk
            while True:
                # Look for "\r\n\r\n" header terminator
                hdr_end = buf.find(b"\r\n\r\n")
                if hdr_end < 0:
                    break
                header = buf[:hdr_end].decode("ascii", errors="ignore")
                # parse Content-Length
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
                # advance past body + trailing CRLF
                buf = buf[body_start + clen + 2 :]
                # Skip leading boundary if present
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
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/test_frame_sampler.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add core/frame_sampler.py tests/test_frame_sampler.py
git commit -m "feat(frame-sampler): MJPEG sampler with deque buffer and throttled backend pushes"
```

---

## Task 6: Persona split — CHOTU.md → BASE + STATELESS + LIVE

**Files:**
- Create: `CHOTU_BASE.md`
- Create: `CHOTU_STATELESS.md`
- Create: `CHOTU_LIVE.md`
- Delete: `CHOTU.md` (after the split is committed)

- [ ] **Step 1: Read the current CHOTU.md**

```bash
wc -l CHOTU.md
cat CHOTU.md
```

Identify three regions:
- **Voice / personality / probability table / physical constraints / examples** → `CHOTU_BASE.md`
- **Heartbeat-rhythm rules**, "after 3 similar ticks change something", empty-turn drop reminders, anything that assumes the 5-8s heartbeat → `CHOTU_STATELESS.md`
- (Nothing yet from the old file goes here — that's a new doc) → `CHOTU_LIVE.md` is authored fresh in Step 4.

- [ ] **Step 2: Write `CHOTU_BASE.md`**

Copy from `CHOTU.md` the sections covering voice, personality probability table, physical constraints (height, gait, servo limits), naming/identity, and any examples that apply regardless of cadence. Strip every reference to "heartbeat", "tick", "every 5 seconds", or "after N ticks". Save as `CHOTU_BASE.md`. Lead with one sentence that says "this file is shared between stateless and live modes."

- [ ] **Step 3: Write `CHOTU_STATELESS.md`**

Copy from `CHOTU.md` only the heartbeat-rhythm rules: the "after 3 similar ticks change something" rule, the empty-turn drop reminder, the existing investigate/explore heartbeat priorities, and any other guidance whose wording assumes "the system asks me what to do every few seconds." Lead with one sentence that says "this file applies only in `PALIV_BRAIN_MODE=stateless`."

- [ ] **Step 4: Write `CHOTU_LIVE.md` from scratch**

The persona for the persistent backend. Required content, in order:

```markdown
# Chotu — Live Mode

This file applies only in `PALIV_BRAIN_MODE=live`. You are running in a
persistent session with continuous vision. The system does NOT prompt you on
a heartbeat — you see frames continuously and choose when to act.

## Rhythm

- Frames stream in at ~1 per second. Do not narrate every frame.
- Stay silent when nothing has changed. Speech costs tokens and gets tedious.
- Emit a tool call or speech when something is genuinely new: a person
  enters frame, an obstacle appears, a known object moves, the user speaks.
- When idle and nothing's changed for ~10 frames, you may pursue your own
  curiosity (look around with `turn`, explore a corner) — but only if
  rested and unbusy. Then go quiet again.

## Parallel actions

You can call `speak`, `face`, `lumos`, and other non-motion tools WHILE a
motion (`move`, `turn`, `set_legs`, `pose`, `trick`) is running. Doing both
at once is encouraged: comment on what you see as you move.

## Motion lock

Only ONE motion tool runs at a time. If you call `move` or `turn` while
another motion is in progress, the tool result will be:

    {"ok": false, "error": "motion in progress: <tool>, ~Xs remaining"}

**When this happens, DO NOT retry.** Observe. Speak if it's useful. Wait
for the current motion to finish (you will see its `ok: true` result in the
stream), then decide whether to replan. Retrying causes oscillation and
wastes turns.

## Wake-up

The first message of every session is "[system] You are awake. Live your
life." There is no user command. React to what you see. Greet whoever is
in frame, or stand and look around if alone.

## You cannot abort a motion

Once a `trick` or `move` starts on the Pi, it runs to completion. You
cannot cancel it mid-step. Plan accordingly: short moves let you react
sooner, long tricks lock you in.
```

- [ ] **Step 5: Verify nothing referenced CHOTU.md by path**

```bash
grep -rn "CHOTU.md" --include="*.py" --include="*.md" . | grep -v "^./docs/" | grep -v "^./CLAUDE.md"
```

Expected: only `core/prompts.py` references it. We'll change that in Task 7.

- [ ] **Step 6: Stage the split (do NOT delete CHOTU.md yet — prompts.py still reads it until Task 7)**

```bash
git add CHOTU_BASE.md CHOTU_STATELESS.md CHOTU_LIVE.md
git commit -m "docs(persona): split CHOTU.md into BASE + STATELESS + LIVE"
```

---

## Task 7: prompts.py per-backend persona selection

**Files:**
- Modify: `core/prompts.py`
- Delete: `CHOTU.md`

- [ ] **Step 1: Rewrite `core/prompts.py`**

```python
"""System prompt loader. Reads PALIV.md (framework) + CHOTU_BASE.md (persona)
plus a mode-specific overlay (CHOTU_STATELESS.md or CHOTU_LIVE.md) based on
PALIV_BRAIN_MODE."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_system_prompt(mode: str | None = None) -> str:
    """Compose PALIV.md + CHOTU_BASE.md + CHOTU_{MODE}.md.

    mode: "stateless" (default) or "live". If None, reads PALIV_BRAIN_MODE env.
    """
    mode = (mode or os.getenv("PALIV_BRAIN_MODE", "stateless")).strip().lower()
    if mode not in ("stateless", "live"):
        raise ValueError(f"PALIV_BRAIN_MODE must be 'stateless' or 'live', got {mode!r}")

    paliv = (REPO_ROOT / "PALIV.md").read_text(encoding="utf-8")
    base = (REPO_ROOT / "CHOTU_BASE.md").read_text(encoding="utf-8")
    overlay_name = "CHOTU_STATELESS.md" if mode == "stateless" else "CHOTU_LIVE.md"
    overlay = (REPO_ROOT / overlay_name).read_text(encoding="utf-8")

    return f"{paliv}\n\n{base}\n\n{overlay}"


# Backwards-compat module-level constant used by brain.py.
# Computed at import time using the env var.
SYSTEM_PROMPT = load_system_prompt()
```

- [ ] **Step 2: Delete the old CHOTU.md**

```bash
git rm CHOTU.md
```

- [ ] **Step 3: Verify the prompt loads in both modes**

```bash
source .venv/bin/activate
PALIV_BRAIN_MODE=stateless python -c "from core.prompts import load_system_prompt as L; s = L(); print(len(s), 'STATELESS' in s.upper())"
PALIV_BRAIN_MODE=live      python -c "from core.prompts import load_system_prompt as L; s = L(); print(len(s), 'LIVE' in s.upper())"
```

Expected: both print non-zero length and `True`.

- [ ] **Step 4: Commit**

```bash
git add core/prompts.py CHOTU.md
git commit -m "feat(prompts): select persona overlay per PALIV_BRAIN_MODE; remove old CHOTU.md"
```

---

## Task 8: LlamaServerBackend — wrap existing LLMClient in the Backend shape

**Files:**
- Create: `core/llama_backend.py`
- Create: `tests/test_llama_backend.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/test_llama_backend.py`:

```python
"""LlamaServerBackend wraps LLMClient. It treats every send_user_text as one
turn: build messages from accumulated context, call chat_complete, emit
AssistantText + ToolCall events. send_frame attaches the frame as a deferred
multimodal user message on the NEXT turn (matching the existing capture_vision
deferral pattern in brain.py)."""

import asyncio
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.llama_backend import LlamaServerBackend
from core.backend import ToolCall, AssistantText


def _fake_llm_response(*, text: str | None = None, tool_calls: list | None = None):
    """Build a fake LLMResponse-shaped object."""
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = tool_calls
    choice = MagicMock(message=msg)
    resp = MagicMock(choices=[choice])
    return resp


async def test_send_user_text_emits_assistant_text():
    llm = MagicMock()
    llm.chat_complete = AsyncMock(return_value=_fake_llm_response(text="hello"))
    llm.format_assistant_message = MagicMock(return_value={"role": "assistant", "content": "hello"})

    b = LlamaServerBackend(llm_client=llm, tool_schemas=[], system_prompt="SYS")
    await b.start()

    events = []
    consumer_done = asyncio.Event()

    async def consume():
        async for ev in b.events():
            events.append(ev)
            if isinstance(ev, AssistantText):
                consumer_done.set()
                break

    consumer = asyncio.create_task(consume())
    await b.send_user_text("hi")
    await asyncio.wait_for(consumer_done.wait(), timeout=2.0)
    consumer.cancel()

    assert any(isinstance(ev, AssistantText) and ev.text == "hello" for ev in events)
    await b.close()


async def test_send_user_text_emits_tool_calls():
    llm = MagicMock()
    tc_mock = MagicMock()
    tc_mock.id = "fc-1"
    tc_mock.function.name = "speak"
    tc_mock.function.arguments = '{"text": "hi"}'
    llm.chat_complete = AsyncMock(return_value=_fake_llm_response(tool_calls=[tc_mock]))
    llm.format_assistant_message = MagicMock(return_value={"role": "assistant", "tool_calls": []})

    b = LlamaServerBackend(llm_client=llm, tool_schemas=[], system_prompt="SYS")
    await b.start()

    got_toolcall = asyncio.Event()
    collected = []

    async def consume():
        async for ev in b.events():
            collected.append(ev)
            if isinstance(ev, ToolCall):
                got_toolcall.set()
                break

    consumer = asyncio.create_task(consume())
    await b.send_user_text("do thing")
    await asyncio.wait_for(got_toolcall.wait(), timeout=2.0)
    consumer.cancel()

    tcs = [e for e in collected if isinstance(e, ToolCall)]
    assert len(tcs) == 1
    assert tcs[0].id == "fc-1"
    assert tcs[0].name == "speak"
    assert tcs[0].args == {"text": "hi"}
    await b.close()


async def test_send_frame_is_buffered_for_next_turn():
    """In stateless mode the frame is queued as a deferred multimodal message
    and attached on the NEXT send_user_text. Verify it's accepted without error."""
    llm = MagicMock()
    llm.chat_complete = AsyncMock(return_value=_fake_llm_response(text="ok"))
    llm.format_assistant_message = MagicMock(return_value={"role": "assistant", "content": "ok"})

    b = LlamaServerBackend(llm_client=llm, tool_schemas=[], system_prompt="SYS")
    await b.start()
    await b.send_frame(b"\xff\xd8\xff\xd9", ts=1.0)  # valid JPEG SOI/EOI
    # No event yet — frame is just buffered
    await b.close()
```

- [ ] **Step 2: Run, verify fails**

```bash
pytest tests/test_llama_backend.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `core/llama_backend.py`**

```python
"""LlamaServerBackend — adapts the existing turn-based LLMClient into the
async Backend protocol used by brain.py in live-mode v1.

In stateless mode, each send_user_text triggers one chat_complete call.
Frames pushed via send_frame are buffered and attached as a deferred
multimodal user message on the NEXT turn (mirroring the deferred-vision
pattern that brain.py already uses for capture_vision results).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import AsyncIterator, Optional

from core.backend import AssistantText, BackendError, Event, SessionEnded, ToolCall

log = logging.getLogger(__name__)


class LlamaServerBackend:
    def __init__(self, *, llm_client, tool_schemas: list[dict], system_prompt: str) -> None:
        self._llm = llm_client
        self._tools = tool_schemas
        self._system = system_prompt
        self._messages: list[dict] = [{"role": "system", "content": system_prompt}]
        self._pending_frames: list[bytes] = []
        self._events: asyncio.Queue[Event] = asyncio.Queue()
        self._closed = False

    async def start(self) -> None:
        return None

    async def send_user_text(self, text: str) -> None:
        user_msg = {"role": "user", "content": text}
        self._messages.append(user_msg)
        # Attach any buffered frames as a follow-up multimodal user message
        if self._pending_frames:
            parts: list[dict] = [
                {"type": "text", "text": f"{len(self._pending_frames)} recent frames, ~1s apart, oldest first."}
            ]
            for jpeg in self._pending_frames:
                b64 = base64.b64encode(jpeg).decode("ascii")
                parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
            self._messages.append({"role": "user", "content": parts})
            self._pending_frames.clear()
        await self._run_turn()

    async def send_frame(self, jpeg_bytes: bytes, ts: float) -> None:
        # Stateless mode buffers; flushed on next send_user_text.
        self._pending_frames.append(jpeg_bytes)
        # Cap pending buffer at 3 to mirror live-mode buffer size
        if len(self._pending_frames) > 3:
            self._pending_frames.pop(0)

    async def send_tool_result(self, tool_call_id: str, result: dict) -> None:
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result),
        })
        # In stateless mode, a tool result triggers a follow-up turn so the
        # model can react to it. This matches the existing brain.py loop.
        await self._run_turn()

    async def events(self) -> AsyncIterator[Event]:
        while not self._closed:
            try:
                ev = await asyncio.wait_for(self._events.get(), timeout=0.5)
                yield ev
            except asyncio.TimeoutError:
                continue
        yield SessionEnded(reason="closed")

    async def close(self) -> None:
        self._closed = True

    async def _run_turn(self) -> None:
        try:
            resp = await self._llm.chat_complete(self._messages, self._tools)
        except Exception as e:
            log.exception("LLM call failed")
            await self._events.put(BackendError(message=str(e), recoverable=True))
            return

        if not resp.choices:
            await self._events.put(BackendError(message="empty choices", recoverable=True))
            return

        msg = resp.choices[0].message
        if msg.content:
            await self._events.put(AssistantText(text=msg.content))

        # Append assistant message for context continuity
        self._messages.append(self._llm.format_assistant_message(resp))

        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            await self._events.put(ToolCall(id=tc.id, name=tc.function.name, args=args))
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/test_llama_backend.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add core/llama_backend.py tests/test_llama_backend.py
git commit -m "feat(backend): LlamaServerBackend adapter — wraps LLMClient into async Backend"
```

---

## Task 9: GeminiLiveBackend — WebSocket adapter

**Files:**
- Create: `core/gemini_live_backend.py`
- Modify: `requirements.txt`
- Modify: `.env.example`

This backend talks to Gemini Live via the official `google-genai` SDK, which exposes a `live.connect()` async context manager over the bidi WebSocket. We implement the same `Backend` protocol shape: open the session at `start()`, push frames/text/tool-results in, drain responses as `Event`s out.

There are no unit tests in this task — the SDK call surface is hard to mock meaningfully and the real verification is the live-mode end-to-end test in Task 13. We keep the surface small and explicit so it's reviewable on its own.

- [ ] **Step 1: Add SDK to requirements**

Append to `requirements.txt`:

```
google-genai>=0.3.0
```

Install:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

- [ ] **Step 2: Add env vars to `.env.example`**

Append to `.env.example`:

```
# Live brain (Gemini 3.1 Flash Live Preview)
PALIV_BRAIN_MODE=stateless        # stateless | live
GEMINI_API_KEY=
PALIV_GEMINI_MODEL=gemini-3.1-flash-live-preview
```

- [ ] **Step 3: Implement `core/gemini_live_backend.py`**

```python
"""GeminiLiveBackend — async Backend over Gemini 3.1 Flash Live Preview's
bidi WebSocket. One session per process. v1 disconnect policy is fail-loud:
on close or error, emit BackendError and let the brain stop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator, Optional

from google import genai
from google.genai import types as gtypes

from core.backend import AssistantText, BackendError, Event, SessionEnded, ToolCall

log = logging.getLogger(__name__)


def _convert_tool_schemas(openai_tools: list[dict]) -> list[gtypes.Tool]:
    """OpenAI function-calling schema → Gemini function declarations."""
    decls = []
    for t in openai_tools or []:
        fn = t.get("function", {})
        decls.append(gtypes.FunctionDeclaration(
            name=fn["name"],
            description=fn.get("description", ""),
            parameters=fn.get("parameters", {"type": "object", "properties": {}}),
        ))
    return [gtypes.Tool(function_declarations=decls)] if decls else []


class GeminiLiveBackend:
    def __init__(
        self,
        *,
        system_prompt: str,
        tool_schemas: list[dict],
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        thinking_level: str = "minimal",
    ) -> None:
        self._system = system_prompt
        self._tools = _convert_tool_schemas(tool_schemas)
        self._model = model or os.getenv("PALIV_GEMINI_MODEL", "gemini-3.1-flash-live-preview")
        self._api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not self._api_key:
            raise ValueError("GEMINI_API_KEY not set")
        self._thinking_level = thinking_level

        self._client = genai.Client(api_key=self._api_key)
        self._session_cm = None     # the live.connect() async-cm
        self._session = None
        self._events: asyncio.Queue[Event] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()

    async def start(self) -> None:
        config = gtypes.LiveConnectConfig(
            response_modalities=["TEXT"],
            system_instruction=gtypes.Content(parts=[gtypes.Part(text=self._system)]),
            tools=self._tools,
            thinking_config=gtypes.ThinkingConfig(thinking_level=self._thinking_level),
            realtime_input_config=gtypes.RealtimeInputConfig(
                automatic_activity_detection=gtypes.AutomaticActivityDetection(disabled=True),
            ),
        )
        self._session_cm = self._client.aio.live.connect(model=self._model, config=config)
        self._session = await self._session_cm.__aenter__()
        self._reader_task = asyncio.create_task(self._reader(), name="GeminiLiveReader")
        log.info("GeminiLiveBackend connected to %s", self._model)

    async def send_user_text(self, text: str) -> None:
        if not self._session:
            return
        await self._session.send_client_content(
            turns=gtypes.Content(role="user", parts=[gtypes.Part(text=text)]),
            turn_complete=True,
        )

    async def send_frame(self, jpeg_bytes: bytes, ts: float) -> None:
        if not self._session:
            return
        await self._session.send_realtime_input(
            media=gtypes.Blob(data=jpeg_bytes, mime_type="image/jpeg"),
        )

    async def send_tool_result(self, tool_call_id: str, result: dict) -> None:
        if not self._session:
            return
        await self._session.send_tool_response(
            function_responses=[gtypes.FunctionResponse(
                id=tool_call_id,
                name=result.get("tool", ""),
                response=result,
            )],
        )

    async def events(self) -> AsyncIterator[Event]:
        while not self._closed.is_set() or not self._events.empty():
            try:
                ev = await asyncio.wait_for(self._events.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            yield ev

    async def close(self) -> None:
        self._closed.set()
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._session_cm:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception as e:
                log.warning("session close error: %s", e)

    async def _reader(self) -> None:
        assert self._session is not None
        try:
            async for response in self._session.receive():
                # Server content (model turns with text parts)
                sc = getattr(response, "server_content", None)
                if sc is not None:
                    mt = getattr(sc, "model_turn", None)
                    if mt is not None:
                        for part in getattr(mt, "parts", []) or []:
                            txt = getattr(part, "text", None)
                            if txt:
                                await self._events.put(AssistantText(text=txt))

                # Tool calls
                tc = getattr(response, "tool_call", None)
                if tc is not None:
                    for fc in getattr(tc, "function_calls", []) or []:
                        await self._events.put(ToolCall(
                            id=getattr(fc, "id", "") or "",
                            name=getattr(fc, "name", "") or "",
                            args=dict(getattr(fc, "args", {}) or {}),
                        ))

                # goAway warning (logged but does not error v1)
                ga = getattr(response, "go_away", None)
                if ga is not None:
                    log.warning("Gemini goAway received: %s", ga)
                    await self._events.put(AssistantText(text="[system] goAway from server — session will end soon"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("Gemini Live reader crashed")
            await self._events.put(BackendError(message=str(e), recoverable=False))
        finally:
            await self._events.put(SessionEnded(reason="reader exited"))
```

- [ ] **Step 4: Smoke-import the module**

```bash
source .venv/bin/activate
python -c "from core.gemini_live_backend import GeminiLiveBackend; print('ok')"
```

Expected: prints `ok` (just verifies imports resolve; no API call).

- [ ] **Step 5: Commit**

```bash
git add core/gemini_live_backend.py requirements.txt .env.example
git commit -m "feat(backend): GeminiLiveBackend over google-genai live.connect"
```

---

## Task 10: Wire MotionLock into tools.py

**Files:**
- Modify: `core/tools.py`
- Modify: `core/brain.py` (only the `dispatch_map = build_dispatch(...)` line)

- [ ] **Step 1: Read the dispatch wrappers in `core/tools.py`**

```bash
grep -n "def build_dispatch\|move\|trick\|set_legs\|^def " core/tools.py | head -40
```

Identify the dispatch wrappers for `move`, `turn`, `set_legs`, `pose`, `trick`. The exact names depend on how `build_dispatch` constructs them.

- [ ] **Step 2: Add MotionLock import + plumbing**

At the top of `core/tools.py`:

```python
from core.motion_lock import MotionLock, MOTION_TOOLS
```

Modify `build_dispatch(...)` signature to accept an optional `motion_lock: MotionLock | None = None`:

```python
def build_dispatch(pi, estop, *, mute: bool = False, motion_lock: MotionLock | None = None) -> dict:
    ...
```

For each motion-tool wrapper inside `build_dispatch`, wrap the body with the lock. Example for `move`:

```python
async def _move(**kw):
    eta = max(1500, kw.get("steps", 1) * 800)  # rough estimate: ~0.8s/step
    if motion_lock is not None:
        rejection = motion_lock.try_acquire("move", kw, eta_ms=eta)
        if rejection is not None:
            return rejection
        async with motion_lock.acquire("move", kw, eta_ms=eta) as ok:
            if not ok:
                # Raced with another caller — re-fetch rejection
                return motion_lock.try_acquire("move", kw, eta_ms=eta) or {
                    "ok": False, "tool": "move", "result": {}, "duration_ms": 0,
                    "timestamp": time.time(), "error": "motion contention",
                }
            return await pi.move(**kw)
    return await pi.move(**kw)
```

Repeat the same pattern for `turn`, `set_legs`, `pose`, `trick`. Use these ETA estimates:

| Tool | eta_ms formula |
|---|---|
| `move` | `max(1500, steps * 800)` |
| `turn` | `max(1500, steps * 800)` (turn shares the move pattern) |
| `set_legs` | `1200` (single pose change) |
| `pose` | `1200` |
| `trick` | `7000` (tricks are 5–10 s per CLAUDE.md) |

- [ ] **Step 3: Update `capture_vision` to read from the FrameSampler buffer**

Find `capture_vision_tool` (or the equivalent dispatch entry) in `core/tools.py`. Add an optional `frame_sampler` injection:

```python
def build_dispatch(pi, estop, *, mute: bool = False, motion_lock=None, frame_sampler=None) -> dict:
    ...
    async def _capture_vision(**kw):
        if frame_sampler is not None:
            jpeg = frame_sampler.latest()
            if jpeg is not None:
                import base64 as _b64
                return {
                    "ok": True, "tool": "capture_vision",
                    "result": {"image_base64": _b64.b64encode(jpeg).decode("ascii")},
                    "duration_ms": 0, "timestamp": time.time(), "error": None,
                }
        # Fall back to the Pi /capture endpoint (which now also reads the buffer Pi-side)
        return await pi.capture()
    ...
```

- [ ] **Step 4: Wire the lock + sampler through `core/brain.py`**

Find the line in `core/brain.py`:

```python
dispatch_map = build_dispatch(pi, estop, mute=MUTE)
```

Replace with:

```python
from core.motion_lock import MotionLock
motion_lock = MotionLock()
# frame_sampler is constructed later in main() once we know the backend.
# It's wired in via a setter; until then capture_vision falls back to /capture.
_frame_sampler_ref = {"sampler": None}

def _get_frame_sampler():
    return _frame_sampler_ref["sampler"]

dispatch_map = build_dispatch(pi, estop, mute=MUTE, motion_lock=motion_lock,
                              frame_sampler=None)  # rewired in main()
```

NOTE: we'll fully wire the sampler in Task 11 when we construct the backend. For now the lock is live and the sampler hook exists.

- [ ] **Step 5: Run the motion-lock tests + existing tests**

```bash
pytest tests/test_motion_lock.py tests/test_backend_protocol.py tests/test_frame_sampler.py tests/test_llama_backend.py -v
```

Expected: all pass.

- [ ] **Step 6: Smoke-import brain**

```bash
python -c "import core.brain; print('ok')"
```

Expected: `ok` printed, no ImportError.

- [ ] **Step 7: Commit**

```bash
git add core/tools.py core/brain.py
git commit -m "feat(tools): wire MotionLock into motion tools and FrameSampler into capture_vision"
```

---

## Task 11: brain.py refactor — producer/consumer with Backend interface

**Files:**
- Modify: `core/brain.py`

The existing `live_loop` + `_process` flow assumes a turn-based LLMClient. We refactor it into:

- **Producer task** — drains `input_queue`, calls `backend.send_user_text(...)` / `send_tool_result(...)`, and sends frames via the FrameSampler (sampler pushes directly to backend).
- **Consumer task** — iterates `backend.events()`, dispatches `ToolCall` events through the existing `dispatch_tool`, queues `AssistantText` for the transcript.

In stateless mode this still produces one round-trip per turn. In live mode the same code paths drive a persistent session.

- [ ] **Step 1: Add backend construction + sampler init in `main()`**

In `core/brain.py main()`, before the `tasks = [...]` block, insert:

```python
from core.frame_sampler import FrameSampler
from core.llama_backend import LlamaServerBackend
from core.gemini_live_backend import GeminiLiveBackend

BRAIN_MODE = os.getenv("PALIV_BRAIN_MODE", "stateless").lower()
print(f"Brain mode: {BRAIN_MODE}")

if BRAIN_MODE == "stateless":
    backend = LlamaServerBackend(
        llm_client=llm_client,
        tool_schemas=TOOL_SCHEMAS,
        system_prompt=SYSTEM_PROMPT,
    )
elif BRAIN_MODE == "live":
    backend = GeminiLiveBackend(
        system_prompt=SYSTEM_PROMPT,
        tool_schemas=TOOL_SCHEMAS,
    )
else:
    raise SystemExit(f"PALIV_BRAIN_MODE must be 'stateless' or 'live', got {BRAIN_MODE!r}")

await backend.start()

stream_url = PI_HOST.rstrip("/") + "/stream"
frame_sampler = FrameSampler(backend=backend, stream_url=stream_url, buffer_size=3, sample_hz=1.0)
_frame_sampler_ref["sampler"] = frame_sampler
# Re-wire dispatch_map so capture_vision can read from the now-live sampler
global dispatch_map
dispatch_map = build_dispatch(pi, estop, mute=MUTE, motion_lock=motion_lock, frame_sampler=frame_sampler)
dispatch_map["explore"] = lambda **kw: dispatch_explore_tool(pi, kw)
await frame_sampler.start()
```

- [ ] **Step 2: Add the consumer task**

After the existing helper functions and before `main()`, add:

```python
async def backend_consumer(backend, dispatch_map_ref):
    """Drain backend.events(). For ToolCall, dispatch and reply via send_tool_result.
    For AssistantText, log + emit to GUI."""
    from core.backend import AssistantText, ToolCall, SessionEnded, BackendError

    async for ev in backend.events():
        if isinstance(ev, AssistantText):
            print_monologue(ev.text)
        elif isinstance(ev, ToolCall):
            print(f"  [tool-call] {ev.name}({ev.args})")
            from core.tools import dispatch_tool
            import json as _json
            try:
                result = await dispatch_tool(dispatch_map_ref, ev.name, _json.dumps(ev.args))
            except Exception as e:
                result = {
                    "ok": False, "tool": ev.name, "result": {}, "duration_ms": 0,
                    "timestamp": time.time(), "error": str(e),
                }
            print_tool_call(ev.name, ev.args, result)
            # Handle speak text-to-speech (was inline in old _process)
            if ev.name == "speak" and result.get("ok"):
                # speak is already routed through dispatch_map (TTS happens there)
                pass
            await backend.send_tool_result(ev.id, result)
        elif isinstance(ev, SessionEnded):
            print(f"  [backend] session ended: {ev.reason}")
            return
        elif isinstance(ev, BackendError):
            print(f"  [backend error] {ev.message}")
            return
```

- [ ] **Step 3: Replace `live_loop` with the producer-style equivalent**

Replace the existing `async def live_loop():` body with:

```python
async def live_loop(backend):
    """Producer: drain input_queue, forward to backend as user-text turns."""
    while True:
        item = await input_queue.get()
        if isinstance(item, str):
            item = wrap_user_input(item)
        text = item.get("text", "").strip()
        if not text:
            continue
        if item.get("kind") in ("user", "event"):
            user_input_pending.clear()
        print(f"\n--- Chotu thinking ({item['kind']}) ---")
        tool_chain_active.set()
        try:
            await backend.send_user_text(text)
        except Exception as e:
            print(f"  [brain error] {e}")
            traceback.print_exc()
        finally:
            tool_chain_active.clear()
        print()
```

- [ ] **Step 4: Wire wake-nudge and tasks in `main()`**

Replace the existing `tasks.append(asyncio.create_task(live_loop()))` and `input_queue.put_nowait(wrap_boot())` lines with:

```python
tasks.append(asyncio.create_task(live_loop(backend)))
tasks.append(asyncio.create_task(backend_consumer(backend, dispatch_map)))

# Wake nudge — one synthetic user turn at startup. In stateless mode this is
# equivalent to the old wrap_boot() message. In live mode it's the cold-start
# trigger so the model emits even with no user command.
if BRAIN_MODE == "live":
    input_queue.put_nowait({"kind": "boot", "text": "[system] You are awake. Live your life."})
else:
    input_queue.put_nowait(wrap_boot())
```

- [ ] **Step 5: Add backend + sampler cleanup in the `finally` block**

In `main()`'s `finally:` block, before `await llm_client.close()`, add:

```python
try:
    await frame_sampler.stop()
except Exception:
    pass
try:
    await backend.close()
except Exception:
    pass
```

- [ ] **Step 6: Smoke-import**

```bash
python -c "import core.brain; print('ok')"
```

Expected: `ok`.

- [ ] **Step 7: Run all tests**

```bash
pytest tests/ -v
```

Expected: all green.

- [ ] **Step 8: Stateless end-to-end smoke test**

In one terminal start llama-server per CLAUDE.md. In another:

```bash
source .venv/bin/activate
PALIV_BRAIN_MODE=stateless PALIV_MUTE=1 python -m core.brain
```

At the `you>` prompt type `hello chotu` and verify:
- Boot wake message produces a response.
- `hello chotu` produces a response with at least one tool call dispatched.
- No exceptions in the log.

Press Ctrl+C to stop. If anything breaks, fix before committing. **This step is the milestone that proves the refactor did not break stateless mode.**

- [ ] **Step 9: Commit**

```bash
git add core/brain.py
git commit -m "feat(brain): producer/consumer refactor over Backend interface; wake nudge + sampler"
```

---

## Task 12: Token accounting for buffered frames

**Files:**
- Modify: `core/brain.py`

The current `_estimate_tokens` ignores image content. With multimodal user messages now flowing through `LlamaServerBackend._messages`, we need the trim guard to know about frame tokens or it will silently exceed budget.

- [ ] **Step 1: Update `_estimate_tokens`**

Find `_estimate_tokens` in `core/brain.py`. Add image-aware counting:

```python
def _estimate_tokens(messages: list[dict]) -> int:
    """Rough token estimate. Text: char/4. Images: 258 per frame (matches
    Gemini's documented per-frame cost; close enough for the local model
    budget heuristic too)."""
    n = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            n += len(content) // 4
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "image_url":
                        n += 258
                    else:
                        txt = part.get("text") or ""
                        n += len(txt) // 4
        for tc in m.get("tool_calls", []) or []:
            args = (tc.get("function") or {}).get("arguments", "")
            n += len(args) // 4
        if m.get("tool_call_id"):
            n += 4
    return n
```

- [ ] **Step 2: Add a regression test**

Create `tests/test_token_accounting.py`:

```python
from core.brain import _estimate_tokens


def test_text_message_counted():
    msgs = [{"role": "user", "content": "a" * 400}]
    assert _estimate_tokens(msgs) == 100  # 400/4


def test_image_part_counted_as_258():
    msgs = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xx"}},
    ]}]
    assert _estimate_tokens(msgs) == 258


def test_mixed_message():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "a" * 40},
        {"type": "image_url", "image_url": {"url": "..."}},
        {"type": "image_url", "image_url": {"url": "..."}},
    ]}]
    assert _estimate_tokens(msgs) == 10 + 258 + 258
```

- [ ] **Step 3: Run, verify pass**

```bash
pytest tests/test_token_accounting.py -v
```

Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add core/brain.py tests/test_token_accounting.py
git commit -m "feat(brain): _estimate_tokens counts image parts at 258 tokens/frame"
```

---

## Task 13: End-to-end live-mode verification

**Files:** None (verification only)

This task does not change code. It verifies the full live-mode loop works end-to-end against the real Gemini Live API. If anything fails here, file the fix as a follow-up task before claiming done.

- [ ] **Step 1: Prepare environment**

```bash
source .venv/bin/activate
export GEMINI_API_KEY=<your-key>
export PALIV_BRAIN_MODE=live
export PALIV_MUTE=1   # avoid TTS during first test
```

Confirm the Pi bridge is running and `/stream` is reachable:

```bash
curl -s --max-time 2 ${PI_HOST:-http://chotu.local:7000}/health
curl -s --max-time 3 ${PI_HOST:-http://chotu.local:7000}/stream | head -c 200
```

Expected: `/health` `ok: true`; `/stream` produces multipart bytes.

- [ ] **Step 2: Start the brain**

```bash
python -m core.brain
```

Watch the log for:
- `Brain mode: live`
- `GeminiLiveBackend connected to gemini-3.1-flash-live-preview`
- Wake nudge sent (`[system] You are awake. Live your life.`)
- Within ~10 s: a `[thinks]` or `[tool-call]` log line — the model has reacted to either the wake nudge or the first frames.

- [ ] **Step 3: Verify frame flow**

In a separate terminal:

```bash
curl -s ${PI_HOST:-http://chotu.local:7000}/stream | head -c 1000 | wc -c
```

Expected: > 500 bytes per second. Frames are flowing.

- [ ] **Step 4: Verify motion lock enforcement (the key Path-B behavior)**

At the `you>` prompt type:

```
do a pushup, and also walk forward 4 steps
```

Watch for:
- A `trick(pushup)` tool call dispatched.
- A `move(...)` tool call attempted while the trick is running.
- The `move` call's result envelope contains `"motion in progress: trick(pushup), ~Xs remaining"`.
- The model **does not** retry immediately; it speaks or waits.

If the model retries, note it as a persona-tuning task and refine `CHOTU_LIVE.md`.

- [ ] **Step 5: Verify obstacle reactivity**

While Chotu is walking, place an object 20 cm in front of it. Expected: within ~2 frames (~2 s), an `AssistantText` event mentions the object or a `speak` tool call comments on it, BEFORE the current move completes. This is the entire point of Path B; if it doesn't happen the persona needs work.

- [ ] **Step 6: Verify disconnect behavior**

Let the session run for 10+ minutes (the natural Gemini Live session limit). Expected: a `goAway` warning appears in the log, then a `SessionEnded` or `BackendError` event, and the loop stops. Confirm the brain process exits cleanly, no hang.

- [ ] **Step 7: Document findings**

Write a short note to `docs/superpowers/specs/2026-06-04-live-brain-design.md` under a new "Phase 1 results" appendix capturing: model cadence (frequent/sparse?), motion-rejection behavior, obstacle reaction latency, any persona tweaks needed. Commit it.

```bash
git add docs/superpowers/specs/2026-06-04-live-brain-design.md
git commit -m "docs(spec): live-mode Phase 1 results appendix"
```

- [ ] **Step 8: Stateless mode regression check**

```bash
PALIV_BRAIN_MODE=stateless PALIV_MUTE=1 python -m core.brain
```

Re-run the same hello-chotu test from Task 11 Step 8 to confirm stateless mode is still healthy after all the changes.

---

## Definition of Done

- [ ] `PALIV_BRAIN_MODE=stateless` runs identically to pre-refactor behavior (Task 11 §8 + Task 13 §8).
- [ ] `PALIV_BRAIN_MODE=live` opens a Gemini Live session, pushes 1 FPS frames, dispatches tool calls (Task 13 §2–3).
- [ ] Wake nudge fires on session open; Chotu acts without a typed command (Task 13 §2).
- [ ] Motion lock rejects overlapping motion with an informative envelope; model does not retry (Task 13 §4).
- [ ] Chotu reacts verbally to a new obstacle mid-move (Task 13 §5).
- [ ] Disconnect (goAway or transport drop) surfaces a clean error and stops the loop (Task 13 §6).
- [ ] All unit tests in `tests/` pass.
- [ ] Phase 1 results appendix written to the spec (Task 13 §7).
