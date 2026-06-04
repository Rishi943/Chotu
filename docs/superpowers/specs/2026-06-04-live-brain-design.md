# Live Brain Design (v1)

**Date:** 2026-06-04
**Status:** Approved for implementation planning
**Predecessors:** `CHOTU_CLOUD_BRAIN_HANDOFF.md` (architectural skeleton, Path A flavor), memory `project-live-brain-pivot` (Path B decision + parallel-tool rule).

## 1. Goal

Add a **persistent, async, vision-grounded** brain mode for Chotu using Gemini 3.1 Flash Live Preview. The current stateless llama-server flow stays exactly as it is. The two modes are sibling backends behind one interface, selected at startup via env var.

The motivation: the local 4B model can follow tool schemas but cannot reason over what it sees. Pet-like reactivity requires (a) a frontier multimodal brain and (b) continuous vision rather than one still per turn.

## 2. Architecture

```
brain.py (loop)
    │  owns: transcript log, frame buffer, motion lock, tool dispatch
    │
    ▼
Backend (abstract)
    ├── LlamaServerBackend   ← wraps existing turn-based flow into async-event shape
    └── GeminiLiveBackend    ← persistent WebSocket, frames pushed continuously
            │
            └── opens 1 WebSocket, sends frame deltas + sensor text,
                emits events as the model produces them
```

Both backends implement the same async interface. The brain loop does not know or care which is active.

## 3. Backend interface

```python
class Backend(Protocol):
    async def start(self) -> None
    async def send_user_text(self, text: str) -> None
    async def send_frame(self, jpeg_bytes: bytes, ts: float) -> None
    async def send_tool_result(self, tool_call_id: str, result: dict) -> None
    async def events(self) -> AsyncIterator[Event]   # ToolCall | AssistantText | SessionEnded | BackendError
    async def close(self) -> None
```

The brain runs **two concurrent tasks**:

- **Producer:** pushes frames at 1 Hz, forwards user text and event injections, sends tool results when they resolve.
- **Consumer:** drains `events()`, dispatches `ToolCall` events through the tool layer, queues `AssistantText` for transcript/logging.

`LlamaServerBackend` adapts the existing per-turn `/v1/chat/completions` call into this shape: `send_user_text` triggers one round-trip, results are pushed onto the event queue. No behavior change for stateless mode.

`GeminiLiveBackend` keeps one WebSocket open for the session's lifetime, forwards `send_*` calls as live messages, surfaces model output as events.

## 4. Frame pipeline (Pi → laptop → backend)

- **New Pi endpoint `/stream`** on `pi_bridge/server.py` — MJPEG over HTTP using `picamera2`. Single shared camera handle; no per-request open/close.
- **Laptop `FrameSampler`** task: connects to `/stream`, decodes at ~1 FPS, drops the latest into a `deque(maxlen=3)`, also calls `backend.send_frame(...)`.
- **`capture_vision` tool stays** but now reads the newest frame from the buffer instead of calling `/capture`. One frame source for the whole system.
- **`/capture` becomes a buffer-read.** The endpoint is repurposed to return the most recent frame the stream sampler grabbed, eliminating camera-handle contention.

## 5. Motion lock (laptop-side)

`core/motion_lock.py` holds a single `asyncio.Lock` plus metadata: `{tool, args, started_at, eta_ms}`.

- Tools `move`, `turn`, `set_legs`, `pose`, `trick`: acquire-or-reject. Rejection returns `{ok: false, error: "motion in progress: trick(pushup), ~6.2s remaining"}` as the tool result, so the model sees it in-context.
- All other tools (`speak`, `face`, `distance`, `capture_vision`, `battery`, `perception`, `lumos`, spells) bypass the lock entirely → free parallelism.

`CHOTU_LIVE.md` must include an explicit rule: **"If a motion call is rejected with 'motion in progress', do not retry. Observe and wait. Replan when the current motion finishes."** This is the only way to prevent oscillation under the reject-and-inform pattern.

### Known v1 limitation: no motion abort

The Pi `/trick`, `/move`, `/turn`, `/set_legs`, `/pose` endpoints run to completion on the Pi side. There is no cancellation. Therefore mid-motion danger reactions ("stop the pushup, there's a dog!") are not possible in v1 — Chotu can only chain-react after the current motion ends. Adding cancellation requires Pi bridge changes and is deferred.

## 6. Speech

Unchanged: model emits text via the `speak` tool, dispatched to existing Piper TTS. Sequential queue, no drop, no max-age. Revisit only if backlog becomes a real problem in testing.

## 7. Persona split

- **`CHOTU_BASE.md`** — voice, personality probability table, physical constraints, naming, examples that apply in both modes.
- **`CHOTU_STATELESS.md`** — heartbeat-rhythm rules ("after 3 similar ticks change something"), empty-turn drop reminders, current behaviors.
- **`CHOTU_LIVE.md`** — continuous-reactivity rules: react to what you see, don't narrate every frame, idle behaviors when nothing changes, parallel-tool guidance ("you can speak while moving"), motion-rejection rule (§5).

`core/prompts.py` selects `PALIV.md + CHOTU_BASE.md + (CHOTU_STATELESS.md | CHOTU_LIVE.md)` based on the active backend.

## 8. Gemini Live config

- `thinkingLevel: "minimal"` default. Reserved escalation hooks (`low`/`medium`) deferred — not in v1.
- No `contextWindowCompression` configured initially (v1 disconnect policy is fail-loudly, see §10). Enable in v2.
- Tools surface = full PALIV tool schemas, same JSON shapes the stateless backend already uses.
- Audio in/out disabled — text in (sensor deltas, wake nudge, user commands), text out (consumed by `speak` tool dispatch), frames in.
- `realtimeInputConfig.automaticActivityDetection.disabled = true` — we control all turn boundaries.

## 9. Token accounting

`_estimate_tokens` adds **+258 tokens per buffered frame**. The trim guard fires on the combined budget so frame-heavy turns can't silently exceed context limits. The figure matches Gemini's documented per-frame cost at default media resolution.

## 10. Disconnect behavior (v1)

**Fail loudly.** On WebSocket disconnect — including the expected ~10-minute Gemini Live session reset — `GeminiLiveBackend.events()` yields `BackendError`. The brain logs and stops the loop. User restarts.

This is deliberate for Phase 1: we want to surface real failure modes and measure how often disconnects actually fire. Auto-reconnect with replay is v2.

Gemini's `goAway` warning (sent ~30 s before the natural reset) is logged visibly in the transcript so we can see disconnects coming rather than being surprised by them.

**Bound:** every v1 live-mode run is capped at the Gemini session lifetime (~10 min). Sufficient for testing, not for an always-on demo. The demo gate is v2.

## 11. Session bootstrap

- New env var `PALIV_BRAIN_MODE=stateless|live` (default `stateless`).
- `brain.py` constructs the corresponding `Backend` at boot. No mid-run swap in v1 (GUI swap button deferred).

### Wake nudge

Gemini Live, like most live models, only emits when given a turn. Frames alone won't trigger a first response. To start a fully autonomous session (no user command), the brain sends **one** synthetic user-text turn at session open:

```
"[system] You are awake. Live your life."
```

After this single nudge the model is free-running. Persona (`CHOTU_LIVE.md`) tells it how to be autonomous. The nudge is sent unconditionally — if the user then types a real command, it's just the next turn.

This mechanism is also how `core/events.py` event injectors (wake_word, battery_low, distance) feed live mode: each injection is a `ClientContent` user-text turn on the open WebSocket, identical to how they're appended to memory in stateless mode.

## 12. What changes, file by file

| File | Change |
|---|---|
| `core/backend.py` | NEW — `Backend` protocol + `Event` types (`ToolCall`, `AssistantText`, `SessionEnded`, `BackendError`) |
| `core/llama_backend.py` | NEW — wraps existing `llm_client` calls into Backend shape |
| `core/gemini_live_backend.py` | NEW — Gemini Live WebSocket adapter, persistent session, frame forwarder |
| `core/frame_sampler.py` | NEW — MJPEG sampler, owns the deque, pushes to active backend |
| `core/motion_lock.py` | NEW — single `asyncio.Lock` + metadata, reject-with-error helper |
| `core/brain.py` | Refactor: producer/consumer task split, drops direct LLM calls, owns FrameSampler + MotionLock. Transcript log restructured to be replay-ready (every send/receive logged with type + timestamp + ids) — sets up v2 reconnect cheaply. |
| `core/tools.py` | Motion-tool wrappers consult `MotionLock`; `capture_vision` reads from buffer |
| `core/prompts.py` | Persona file selection per backend |
| `pi_bridge/server.py` | NEW `/stream` MJPEG endpoint; shared camera handle; `/capture` repurposed to return latest buffered frame |
| `CHOTU.md` | Split into `CHOTU_BASE.md` + `CHOTU_STATELESS.md` + `CHOTU_LIVE.md` |
| `.env.example` | Add `PALIV_BRAIN_MODE`, `GEMINI_API_KEY` |

## 13. Definition of done

1. `PALIV_BRAIN_MODE=stateless` runs identically to current `main`.
2. `PALIV_BRAIN_MODE=live` opens a Gemini Live session, frames flow at ~1 FPS, model emits text + tool calls between turns, motion lock enforces single-motion, speech plays via Piper.
3. Wake nudge fires on session open; Chotu acts autonomously without a typed command.
4. Manual test: Chotu sees an obstacle while walking and reacts (verbally, since motion abort is out of scope) **before** the current move completes. This is the whole point of Path B — verify it.
5. Disconnect (including the natural ~10-min reset) surfaces a clean error, loop stops, user can restart.
6. Transcript log captures every WS direction (send/receive) with timestamps and tool-call ids, sufficient to support v2 replay without further changes to logging.

## 14. Out of scope (explicit)

- Auto-reconnect with context replay (v2).
- Mid-run backend swap GUI button (v2).
- Motion cancellation on the Pi (separate spec).
- `contextWindowCompression` and unlimited-duration sessions (v2).
- Hot-swapping persona mid-session (Gemini Live `systemInstruction` is set once at setup; this is a documented constraint, not a v1 omission).
- `QwenOmniBackend` cross-vendor validation (stretch goal in handoff doc; defer until v1 lands).
- Reasoning-effort escalation (`thinkingLevel: low/medium/high`).

## 15. Open risks (not blocking, flagged for Phase 1 monitoring)

- **Motion-rejection oscillation** (§5): persona must teach "observe and wait." Behavior is testable but unverified.
- **Model emission cadence** is opaque — tuned entirely via persona text. Build the transcript viewer early; expect iteration.
- **Tool-response ordering**: speak (fast) and move (slow) tool results return out of order. Gemini Live tolerates this per docs; sanity-check with a small spike before the full integration.
- **Frame backpressure** at 1 FPS over WS is unlikely to matter but warrants monitoring.
