# Push-to-Talk + Hands-Free GUI Voice Input — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a segmented `🎤 | ∞` button to the browser GUI: tap 🎤 for a one-shot push-to-talk utterance, toggle ∞ for a latched hands-free conversation mode — both feeding the existing `pending_input` buffer.

**Architecture:** Reuse the existing capture stack (`core/voice.py` `VoiceListener` + energy-VAD `record_utterance` + Whisper) and GUI transport (FastAPI :8888, SSE `/events`, `pending_input`). `brain.py` owns orchestration (it holds `pending_input`, `gui_event_queue`, `tts_done_event`); `gui_server.py` adds thin endpoints; `index.html` adds the pill. Gated by a new `PALIV_PTT=1` flag, independent of the wake-word `voice_loop`.

**Tech Stack:** Python 3.12, FastAPI/Starlette `TestClient`, pytest (`asyncio_mode = auto`), `unittest.mock.AsyncMock`, sounddevice + faster-whisper (capture, manually verified), vanilla JS + SSE (browser).

**Spec:** `docs/superpowers/specs/2026-06-09-push-to-talk-gui-design.md`

**Commit messages:** plain — **do NOT add any `Co-Authored-By` / "Generated with Claude" trailer** (user preference).

**Branch:** work on `push-to-talk-gui` (already created, spec committed there).

---

## File structure

| File | Responsibility |
|---|---|
| `core/voice.py` | Capture primitives. Add one-shot `record_push_to_talk()`. |
| `core/brain.py` | Orchestration: `PTT_ENABLED` flag, one-shot `trigger_ptt_capture()`, hands-free `set_handsfree()` + `_handsfree_loop()`. |
| `core/gui_server.py` | HTTP surface: `POST /ptt`, `POST /handsfree`, `GET /api/config`. |
| `core/static/index.html` | The `🎤 | ∞` pill: markup, CSS, click handlers, SSE `ptt` state mapping. |
| `tests/test_ptt.py` | Brain one-shot + hands-free unit tests (capture monkeypatched). |
| `tests/test_gui_ptt.py` | Endpoint tests via `TestClient` (brain fns mocked). |

---

## Task 1: One-shot capture primitive in `voice.py`

**Files:**
- Modify: `core/voice.py` (add two functions near the existing `listen_and_transcribe` at the bottom)
- Test: `tests/test_ptt.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_ptt.py`:

```python
import asyncio
import core.voice as voice


class _FakeListener:
    """Records call order; returns a canned utterance from record_utterance."""
    instances = []

    def __init__(self):
        self.calls = []
        _FakeListener.instances.append(self)

    def start(self):
        self.calls.append("start")

    def drain(self):
        self.calls.append("drain")

    def record_utterance(self):
        self.calls.append("record")
        return "walk forward"

    def stop(self):
        self.calls.append("stop")


def test_record_push_to_talk_drains_records_stops(monkeypatch):
    _FakeListener.instances = []
    monkeypatch.setattr(voice, "VoiceListener", _FakeListener)
    text = asyncio.run(voice.record_push_to_talk())
    assert text == "walk forward"
    listener = _FakeListener.instances[0]
    # opens, drains stale audio, records, always closes — in that order
    assert listener.calls == ["start", "drain", "record", "stop"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_ptt.py -k record_push_to_talk -v`
Expected: FAIL — `AttributeError: module 'core.voice' has no attribute 'record_push_to_talk'`.

- [ ] **Step 3: Implement**

In `core/voice.py`, after the existing `listen_and_transcribe` function at the end of the file, add:

```python
def _blocking_record_once() -> str:
    """One-shot: drain → record one utterance (VAD stop) → transcribe. No wake word.
    Opens and closes its own stream."""
    listener = VoiceListener()
    listener.start()
    try:
        listener.drain()
        return listener.record_utterance()
    finally:
        listener.stop()


async def record_push_to_talk() -> str:
    """Async wrapper: run the blocking one-shot capture in a thread. Returns text or ''."""
    return await asyncio.to_thread(_blocking_record_once)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_ptt.py -k record_push_to_talk -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/voice.py tests/test_ptt.py
git commit -m "feat(voice): record_push_to_talk — one-shot capture without wake word"
```

---

## Task 2: One-shot orchestration in `brain.py`

**Files:**
- Modify: `core/brain.py` (add `PTT_ENABLED` near the other flags ~line 36; add globals + `trigger_ptt_capture` after `strip_internal_fields` ~line 50, before `# --- Globals ---`, OR in the Globals/Input-loops region — place the function after `_emit` is defined so it can emit)
- Test: `tests/test_ptt.py`

Note: `trigger_ptt_capture` calls `_emit(...)` and `pending_input.push(...)`, both defined in `brain.py`. Place the new function AFTER `_emit` (defined ~line 113) — e.g. just before the `# --- Input loops ---` section (~line 359). Place the `PTT_ENABLED` flag with the other `os.getenv` flags (~line 36) and the two new globals (`_ptt_capturing`, `handsfree_task`) in the `# --- Globals ---` block (~line 67).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ptt.py`:

```python
import core.brain as brain


def _drain_events():
    states = []
    while True:
        try:
            states.append(brain.gui_event_queue.get_nowait())
        except Exception:
            break
    return [e.get("state") for e in states if e.get("type") == "ptt"]


def _reset_brain():
    brain._ptt_capturing = False
    brain.handsfree_task = None
    brain.pending_input.drain()
    _drain_events()


def test_trigger_ptt_capture_pushes_text_and_brackets_events(monkeypatch):
    _reset_brain()

    async def _fake():
        return "look left"
    monkeypatch.setattr("core.voice.record_push_to_talk", _fake)

    asyncio.run(brain.trigger_ptt_capture())
    assert brain.pending_input.drain() == "look left"
    assert _drain_events() == ["recording", "idle"]


def test_trigger_ptt_capture_no_push_on_empty(monkeypatch):
    _reset_brain()

    async def _fake():
        return "   "
    monkeypatch.setattr("core.voice.record_push_to_talk", _fake)

    asyncio.run(brain.trigger_ptt_capture())
    assert brain.pending_input.drain() is None


def test_trigger_ptt_capture_single_flight_when_already_capturing(monkeypatch):
    _reset_brain()
    brain._ptt_capturing = True  # a capture is "in progress"

    async def _fake():
        return "should not run"
    monkeypatch.setattr("core.voice.record_push_to_talk", _fake)

    asyncio.run(brain.trigger_ptt_capture())
    assert brain.pending_input.drain() is None


def test_trigger_ptt_capture_ignored_when_handsfree_active(monkeypatch):
    _reset_brain()
    brain.handsfree_task = object()  # hands-free loop "running"

    async def _fake():
        return "should not run"
    monkeypatch.setattr("core.voice.record_push_to_talk", _fake)

    asyncio.run(brain.trigger_ptt_capture())
    assert brain.pending_input.drain() is None
    brain.handsfree_task = None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_ptt.py -k trigger_ptt_capture -v`
Expected: FAIL — `AttributeError: module 'core.brain' has no attribute 'trigger_ptt_capture'` (and `_ptt_capturing` / `handsfree_task`).

- [ ] **Step 3: Implement**

In `core/brain.py`, add the flag with the other `os.getenv` config flags (near line 36, after `LOOP_FLOOR`):

```python
PTT_ENABLED = os.getenv("PALIV_PTT", "0") == "1"   # GUI push-to-talk + hands-free button
```

Add the two globals in the `# --- Globals ---` block (near line 67, after `_pi_reachable = False`):

```python
_ptt_capturing: bool = False           # single-flight guard for one-shot push-to-talk
handsfree_task: "asyncio.Task | None" = None  # running hands-free loop, or None
```

Add the function just before the `# --- Input loops ---` section (near line 359):

```python
# --- Push-to-talk (GUI) ---

async def trigger_ptt_capture() -> None:
    """One-shot push-to-talk. No-op if a capture is already running or hands-free is on.
    Records one utterance (VAD stop) and pushes the transcript to pending_input."""
    global _ptt_capturing
    if _ptt_capturing or handsfree_task is not None:
        return
    _ptt_capturing = True
    _emit({"type": "ptt", "state": "recording"})
    try:
        from core.voice import record_push_to_talk
        text = await record_push_to_talk()
        if text.strip():
            pending_input.push(text)
    except Exception as e:
        print(f"  [ptt error] {e}")
    finally:
        _ptt_capturing = False
        _emit({"type": "ptt", "state": "idle"})
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_ptt.py -k trigger_ptt_capture -v`
Expected: PASS (4 tests). Then `python -c "import core.brain"` (exit 0).

- [ ] **Step 5: Commit**

```bash
git add core/brain.py tests/test_ptt.py
git commit -m "feat(ptt): one-shot trigger_ptt_capture with single-flight guard + state events"
```

---

## Task 3: Hands-free loop in `brain.py`

**Files:**
- Modify: `core/brain.py` (add `set_handsfree` + `_handsfree_loop` right after `trigger_ptt_capture`)
- Test: `tests/test_ptt.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ptt.py`:

```python
class _HFListener:
    """Fake VoiceListener for the hands-free loop: yields one utterance then ''."""
    def __init__(self):
        self._n = 0

    def start(self): pass
    def stop(self): pass
    def drain(self): pass

    def record_utterance(self):
        self._n += 1
        return "come here" if self._n == 1 else ""


def test_set_handsfree_starts_pushes_and_stops(monkeypatch):
    _reset_brain()
    monkeypatch.setattr("core.voice.VoiceListener", _HFListener)

    async def _run():
        brain.tts_done_event.set()        # allow turn-taking to proceed
        brain.set_handsfree(True)
        assert brain.handsfree_task is not None
        await asyncio.sleep(0.1)           # let one capture happen
        got = brain.pending_input.drain()
        brain.set_handsfree(False)
        await asyncio.sleep(0.05)          # let cancellation + finally run
        return got

    got = asyncio.run(_run())
    assert got == "come here"
    assert brain.handsfree_task is None
    states = _drain_events()
    assert "handsfree_on" in states
    assert states[-1] == "handsfree_off"


def test_set_handsfree_false_is_noop_when_not_running():
    _reset_brain()
    brain.set_handsfree(False)            # must not raise
    assert brain.handsfree_task is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_ptt.py -k handsfree -v`
Expected: FAIL — `AttributeError: module 'core.brain' has no attribute 'set_handsfree'`.

- [ ] **Step 3: Implement**

In `core/brain.py`, immediately after `trigger_ptt_capture`, add:

```python
def set_handsfree(enabled: bool) -> None:
    """Start or stop the hands-free conversation loop (idempotent)."""
    global handsfree_task
    if enabled and handsfree_task is None:
        handsfree_task = asyncio.create_task(_handsfree_loop())
    elif not enabled and handsfree_task is not None:
        handsfree_task.cancel()
        handsfree_task = None


async def _handsfree_loop() -> None:
    """Latched hands-free: record an utterance, push it, wait for Chotu to finish
    speaking, repeat. No silence timeout — runs until set_handsfree(False) cancels it."""
    from core.voice import VoiceListener
    listener = VoiceListener()
    listener.start()
    _emit({"type": "ptt", "state": "handsfree_on"})
    first = True
    try:
        while True:
            if not first:
                await tts_done_event.wait()   # let Chotu finish before listening again
                tts_done_event.clear()
            first = False
            listener.drain()
            _emit({"type": "ptt", "state": "handsfree_listening"})
            text = await asyncio.to_thread(listener.record_utterance)
            _emit({"type": "ptt", "state": "handsfree_on"})
            if text.strip():
                pending_input.push(text)
    except asyncio.CancelledError:
        pass
    finally:
        listener.stop()
        _emit({"type": "ptt", "state": "handsfree_off"})
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_ptt.py -k handsfree -v`
Expected: PASS (2 tests). Then full file: `python -m pytest tests/test_ptt.py -v` (all green).

- [ ] **Step 5: Commit**

```bash
git add core/brain.py tests/test_ptt.py
git commit -m "feat(ptt): hands-free conversation loop (manual stop, TTS turn-taking)"
```

---

## Task 4: GUI endpoints in `gui_server.py`

**Files:**
- Modify: `core/gui_server.py` (add three routes near the existing `/stt` route ~line 88)
- Test: `tests/test_gui_ptt.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gui_ptt.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

import core.brain as brain
from core.gui_server import app

client = TestClient(app)


def test_config_reports_flag(monkeypatch):
    monkeypatch.setattr(brain, "PTT_ENABLED", True)
    assert client.get("/api/config").json() == {"ptt_enabled": True}
    monkeypatch.setattr(brain, "PTT_ENABLED", False)
    assert client.get("/api/config").json() == {"ptt_enabled": False}


def test_ptt_endpoint_triggers_capture_when_enabled(monkeypatch):
    monkeypatch.setattr(brain, "PTT_ENABLED", True)
    mock = AsyncMock()
    monkeypatch.setattr(brain, "trigger_ptt_capture", mock)
    resp = client.post("/ptt")
    assert resp.json() == {"ok": True}
    assert mock.called


def test_ptt_endpoint_disabled(monkeypatch):
    monkeypatch.setattr(brain, "PTT_ENABLED", False)
    mock = AsyncMock()
    monkeypatch.setattr(brain, "trigger_ptt_capture", mock)
    resp = client.post("/ptt")
    assert resp.json()["ok"] is False
    assert not mock.called


def test_handsfree_endpoint_sets_mode(monkeypatch):
    monkeypatch.setattr(brain, "PTT_ENABLED", True)
    mock = MagicMock()
    monkeypatch.setattr(brain, "set_handsfree", mock)
    resp = client.post("/handsfree", json={"enabled": True})
    assert resp.json() == {"ok": True}
    mock.assert_called_once_with(True)


def test_handsfree_endpoint_disabled(monkeypatch):
    monkeypatch.setattr(brain, "PTT_ENABLED", False)
    mock = MagicMock()
    monkeypatch.setattr(brain, "set_handsfree", mock)
    resp = client.post("/handsfree", json={"enabled": True})
    assert resp.json()["ok"] is False
    assert not mock.called
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_gui_ptt.py -v`
Expected: FAIL — 404s / missing routes (`/ptt`, `/handsfree`, `/api/config`).

- [ ] **Step 3: Implement**

In `core/gui_server.py`, after the existing `/stt` route (ends ~line 93), add:

```python
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
    return JSONResponse({"ptt_enabled": brain.PTT_ENABLED})
```

(`asyncio`, `Request`, `JSONResponse`, and `brain` are already imported at the top of `gui_server.py`.)

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_gui_ptt.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add core/gui_server.py tests/test_gui_ptt.py
git commit -m "feat(ptt): GUI endpoints /ptt, /handsfree, /api/config (flag-gated)"
```

---

## Task 5: The `🎤 | ∞` pill in `index.html`

**Files:**
- Modify: `core/static/index.html` (CSS before `</style>` line 380; markup after the send button line 429; SSE branch + init JS in the script)

This task is UI; it is verified by a grep sanity check plus a manual browser check (no unit test — the brain/endpoint logic it drives is already covered by Tasks 2–4).

- [ ] **Step 1: Add the pill CSS**

In `core/static/index.html`, immediately before the closing `</style>` (line 380), add:

```css
    .ptt-pill { display:none; align-items:center; flex:none; border-radius:20px;
      overflow:hidden; border:1px solid #34343c; margin-left:8px; cursor:pointer; }
    .ptt-seg { width:38px; height:38px; display:flex; align-items:center;
      justify-content:center; background:#202028; color:#cfcfe0; font-size:15px;
      user-select:none; }
    .ptt-seg + .ptt-seg { border-left:1px solid #34343c; }
    #ptt-mic.active { background:#1d2b44; color:#cfe2ff; }
    #ptt-hf.active  { background:#3a1320; color:#ffd9df; }
```

- [ ] **Step 2: Add the pill markup**

In `core/static/index.html`, immediately after the send button line:
`<button class="send-btn" onclick="sendInput()">send</button>` (line 429), add:

```html
      <div class="ptt-pill" id="ptt-pill">
        <div class="ptt-seg" id="ptt-mic" title="Tap to talk once">🎤</div>
        <div class="ptt-seg" id="ptt-hf" title="Hands-free conversation">∞</div>
      </div>
```

- [ ] **Step 3: Add the SSE `ptt` branch**

In `core/static/index.html`, find the battery branch in `es.onmessage`:

```javascript
      } else if (type === 'battery') {
        applyBattery(data.percent, data.voltage);
      }
```

Replace it with (adds the `ptt` branch after battery):

```javascript
      } else if (type === 'battery') {
        applyBattery(data.percent, data.voltage);
      } else if (type === 'ptt') {
        var mic = document.getElementById('ptt-mic');
        var hf = document.getElementById('ptt-hf');
        if (!mic || !hf) return;
        var s = data.state;
        if (s === 'recording' || s === 'handsfree_listening') {
          mic.classList.add('active');
        } else {
          mic.classList.remove('active');
        }
        if (s === 'handsfree_on' || s === 'handsfree_listening') {
          hf.classList.add('active');
        } else if (s === 'handsfree_off') {
          hf.classList.remove('active');
        }
      }
```

- [ ] **Step 4: Add the pill init JS**

In `core/static/index.html`, after the continuous-checkbox block (the `continuousCb.addEventListener(...)` block ending ~line 612), add:

```javascript
  // ── Push-to-talk pill ─────────────────────────────────────
  (function initPtt() {
    fetch('/api/config').then(function(r) { return r.json(); }).then(function(cfg) {
      if (!cfg.ptt_enabled) return;
      var pill = document.getElementById('ptt-pill');
      var mic = document.getElementById('ptt-mic');
      var hf = document.getElementById('ptt-hf');
      pill.style.display = 'flex';
      var handsfree = false;
      mic.addEventListener('click', function() {
        fetch('/ptt', { method: 'POST' }).catch(function() {});
      });
      hf.addEventListener('click', function() {
        handsfree = !handsfree;
        fetch('/handsfree', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: handsfree })
        }).catch(function() {});
      });
    }).catch(function() {});
  })();
```

- [ ] **Step 5: Sanity-check the edits**

Run: `grep -n "ptt-pill\|ptt-mic\|ptt-hf\|initPtt\|/api/config\|type === 'ptt'" core/static/index.html`
Expected: matches for the CSS ids, the markup ids, the init function, the config fetch, and the SSE branch (≥7 lines).

- [ ] **Step 6: Manual browser verification**

Run the brain with PTT on (requires llama-server up and the voice deps installed):
```bash
source .venv/bin/activate && PALIV_PTT=1 PALIV_MUTE=1 python3 -m core.brain
```
Open `http://localhost:8888`. Confirm: the `🎤 | ∞` pill appears right of "send"; tapping 🎤 turns it blue and (after you speak + pause) your words appear as a `[user]` line; tapping ∞ turns it red and stays red across turns until tapped off. If no mic/Whisper is available, at minimum confirm the pill renders and the `/ptt` POST fires (check the brain log for `[ptt ...]` or the network tab).

- [ ] **Step 7: Commit**

```bash
git add core/static/index.html
git commit -m "feat(ptt): segmented mic|infinity pill in GUI input bar"
```

---

## Final verification

- [ ] **Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all green, including `tests/test_ptt.py` and `tests/test_gui_ptt.py`.

- [ ] **Import smoke-check**

Run: `python -c "import core.voice, core.brain, core.gui_server"`
Expected: exit 0.

---

## Self-review notes (author)

- **Spec §A** (voice one-shot) → Task 1.
- **Spec §B** (`PTT_ENABLED`, `trigger_ptt_capture`, single-flight, mutual exclusion) → Task 2.
- **Spec §C** (`set_handsfree`, `_handsfree_loop`, manual stop, `tts_done_event` turn-taking) → Task 3.
- **Spec §D** (`/ptt`, `/handsfree`, `/api/config`, gating) → Task 4.
- **Spec §E** (pill markup, CSS, click handlers, SSE `ptt` mapping, config-gated visibility) → Task 5.
- **Spec SSE contract** (`recording | idle | handsfree_on | handsfree_listening | handsfree_off`) → emitted in Tasks 2–3, consumed in Task 5. States match across all three.
- **Naming consistency:** `PTT_ENABLED`, `_ptt_capturing`, `handsfree_task`, `trigger_ptt_capture`, `set_handsfree`, `_handsfree_loop`, `record_push_to_talk`, ids `ptt-pill`/`ptt-mic`/`ptt-hf` — used identically across tasks and the spec.
- **Out of scope (non-goals):** TTS interrupt/barge-in, browser-side mic, changes to `voice_loop` / `/stt` continuous mode.
