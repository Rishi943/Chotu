# Push-to-Talk + Hands-Free Voice Input (GUI) — Design

**Date:** 2026-06-09
**Goal:** Let the user talk to Chotu from the browser GUI via one segmented button:
a quick **tap-to-talk** (one utterance) and a latched **hands-free** mode (flowing
conversation, for video shoots). Terminal and browser text input keep working.

**Decisions locked with user:**
- Button = **Concept C**, a segmented `🎤 | ∞` pill in the GUI input bar (right of "send").
- One-shot stop = **VAD** (existing silence detection). Hands-free stop = **manual only**
  (tap `∞` off; the existing 30s continuous auto-drop is disabled for this path).
- Hands-free label = the `∞` infinity glyph.
- New flag **`PALIV_PTT=1`** gates the feature, independent of `PALIV_VOICE`.

This reuses the existing capture/transcribe stack (`core/voice.py` `VoiceListener` +
`record_utterance` energy-VAD + Whisper) and the existing GUI transport (FastAPI on 8888,
SSE `/events`, `pending_input` buffer). The wake-word `voice_loop` is untouched.

---

## Architecture

```
[browser pill]                 [gui_server :8888]            [brain (same process)]
  tap 🎤  ── POST /ptt ───────► create_task(trigger_ptt_capture())
  tap ∞   ── POST /handsfree ─► set_handsfree(enabled)
  load    ── GET  /api/config ► {"ptt_enabled": PTT_ENABLED}
       ◄──────── SSE /events ── gui_event_queue  ◄── ptt state events
```

`brain` owns orchestration because it holds `pending_input`, `gui_event_queue`, and
`tts_done_event`. `gui_server` stays a thin HTTP layer. `voice` stays the capture
primitive. Spoken text enters the same `pending_input` buffer the paced loop drains, so
`run_iteration`'s existing `{"type":"user",...}` emit shows the transcript in the GUI with
no extra wiring.

---

## Components

### A. `core/voice.py` — one-shot capture
Add a public async function (the existing one-shot shape minus the wake word):

```python
async def record_push_to_talk() -> str:
    """Open the mic, drain any buffered audio, record one utterance (energy-VAD stop
    or MAX_RECORD_S cap), transcribe, close. Returns text or ''. No wake word."""
    return await asyncio.to_thread(_blocking_record_once)

def _blocking_record_once() -> str:
    listener = VoiceListener()
    listener.start()
    try:
        listener.drain()
        return listener.record_utterance()
    finally:
        listener.stop()
```

Hands-free reuses the **public** `VoiceListener` + `record_utterance()` directly (see C);
no change to existing wake-word code (`listen_and_transcribe`, `wait_wake_word`).

### B. `core/brain.py` — config + one-shot
```python
PTT_ENABLED = os.getenv("PALIV_PTT", "0") == "1"

_ptt_capturing: bool = False          # single-flight guard for one-shot
handsfree_task: asyncio.Task | None = None
```

```python
async def trigger_ptt_capture() -> None:
    """One-shot push-to-talk. Ignored if a capture is already running or hands-free is on."""
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

### C. `core/brain.py` — hands-free loop
```python
def set_handsfree(enabled: bool) -> None:
    """Start/stop the hands-free conversation loop (idempotent)."""
    global handsfree_task
    if enabled and handsfree_task is None:
        handsfree_task = asyncio.create_task(_handsfree_loop())
    elif not enabled and handsfree_task is not None:
        handsfree_task.cancel()
        handsfree_task = None

async def _handsfree_loop() -> None:
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

Notes:
- **No silence timeout** — runs until `set_handsfree(False)` cancels it (manual stop).
- Turn-taking via `tts_done_event` mirrors the existing continuous branch in `voice_loop`,
  so the user and Chotu alternate and the mic doesn't record Chotu's own TTS.
- One-shot and hands-free are mutually exclusive: `trigger_ptt_capture` no-ops while
  `handsfree_task` is live.

### D. `core/gui_server.py` — endpoints (mirror existing `/stt`, `/chat`)
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

The existing `/stt` + `continuous_mode` (wake-word continuous mode, used only by
`voice_loop` under `PALIV_VOICE=1`) is left as-is — it is a separate feature from this
GUI hands-free path.

### E. `core/static/index.html` — the pill
- On load, `fetch('/api/config')`; show the pill only when `ptt_enabled`.
- Markup: a two-segment pill (`🎤` left, `∞` right) appended to `.input-bar` after `send`.
- `🎤` click → `POST /ptt`. `∞` click → toggle local state, `POST /handsfree {enabled}`.
- Extend the existing SSE `switch(data.type)` with a `case "ptt"` that maps `data.state`:
  - `recording` → 🎤 segment blue/active; `idle` → reset.
  - `handsfree_on` → ∞ segment red/active; `handsfree_listening` → 🎤 segment pulses;
    `handsfree_off` → reset both.
- Visual styling follows the approved Concept C mockup (blue = one-shot listening,
  red = hands-free live).

---

## SSE event contract (brain → button)

`{"type": "ptt", "state": S}` where `S` ∈
`recording | idle | handsfree_on | handsfree_listening | handsfree_off`.

---

## Error handling & limits
- Capture/transcribe exception → log, reset `_ptt_capturing`, emit `idle`; hands-free
  cancellation/exception → `finally` closes the mic and emits `handsfree_off`. Brain
  loop unaffected.
- Missing voice deps (`faster_whisper`/`sounddevice`) → the import inside the capture
  raises, caught and logged; the endpoint already returned `{"ok":true}`, so the UI
  resets on the `idle`/`off` event. (`/api/config` still reports enabled = flag value.)
- **Known v1 limit:** one-shot 🎤 does not stop in-flight TTS, so tapping mid-speech may
  capture Chotu's own voice (`drain` clears only pre-tap buffer). Hands-free avoids this
  via the `tts_done_event` wait. Aborting in-flight TTS is the separate interrupt feature
  (audit T3.2) — out of scope here.

---

## Testing (hardware-free)

New `tests/test_ptt.py`, monkeypatching the capture functions so no mic/Whisper is needed:

1. **One-shot guard & push:** patch `core.voice.record_push_to_talk` to return a fixed
   string; assert `trigger_ptt_capture` pushes non-empty text to `pending_input`, does
   **not** push on `''`, and a second concurrent call is a no-op while `_ptt_capturing`
   (and while `handsfree_task` is set). Assert `recording`→`idle` events bracket.
2. **Hands-free start/stop:** `set_handsfree(True)` creates a task; `set_handsfree(False)`
   cancels it and clears `handsfree_task`. With `VoiceListener.record_utterance`
   monkeypatched to yield one canned utterance then block, assert one push occurs, the
   loop awaits `tts_done_event` before a second capture, and `handsfree_on`/`handsfree_off`
   events are emitted.
3. **Endpoints:** with `brain.trigger_ptt_capture` / `brain.set_handsfree` patched, assert
   `POST /ptt` and `POST /handsfree` call them and return `ok`; when `PTT_ENABLED` is
   False both return `{"ok": false}`. `GET /api/config` reflects the flag.

The mic→Whisper path itself stays a manual check (`PALIV_PTT=1`, click the pill).

---

## Files touched
| File | Change |
|---|---|
| `core/voice.py` | + `record_push_to_talk()` / `_blocking_record_once()` |
| `core/brain.py` | + `PTT_ENABLED`, `_ptt_capturing`, `handsfree_task`, `trigger_ptt_capture()`, `set_handsfree()`, `_handsfree_loop()` |
| `core/gui_server.py` | + `POST /ptt`, `POST /handsfree`, `GET /api/config` |
| `core/static/index.html` | + segmented pill, click handlers, `case "ptt"` SSE, config-gated visibility |
| `tests/test_ptt.py` | new — one-shot, hands-free, endpoints (all faked) |

## Non-goals
- Stopping/interrupting in-flight TTS (audit T3.2).
- Browser-side mic capture / WebRTC — audio is captured on the laptop via `sounddevice`.
- Changes to the wake-word `voice_loop` / `PALIV_VOICE` path or `/stt` continuous mode.
- ReSpeaker-specific tuning (not yet ordered; laptop default mic only).
