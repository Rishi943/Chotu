# Chotu — Cloud Brain Handoff

This doc brings a Claude Code session up to speed on a **specific architectural decision**: moving Chotu's brain from a local stateless LLM to a persistent-session cloud model (Gemini Live / Qwen Omni Realtime) with a live video feed, while keeping the harness able to run local models later.

It assumes you already have `PALIV.md`, `CHOTU.md`, `brain.py`, `tools.py`, `heartbeat.py`, `prompts.py`, `habits.py` in the repo. This is the *delta* on top of those, not a replacement.

---

## TL;DR of the decision

The local 4B model can follow tool schemas but cannot reason over what it sees — multimodal grounding is the wall, not raw intelligence. When the same MD files + tools were driven by a frontier model (via CC), Chotu invented new `set_legs` gaits on request and recovered from a Pi bridge restart in ~2 min. That capability needs a frontier-class brain with real vision.

**Plan: cloud-first.** Prove the architecture works on Gemini Live with a live video feed. Then local becomes a model swap when hardware/small-models catch up. If it runs on cloud, someone with the hardware can run it locally.

---

## The two interaction models

| | Stateless (current) | Persistent (Gemini Live / Qwen Omni Realtime) |
|---|---|---|
| Memory | Harness holds it, resends every turn | The open session IS the memory |
| Transport | HTTP `/v1/chat/completions` | WebSocket, stays open |
| Per-turn payload | Full context every time | Delta only (new frames + new text) |
| Video | One still per turn (`capture_vision`) | Continuous frames into the session |
| llama-server | Native | Cannot do this natively |

---

## Core architectural rule: **stateless-first harness**

Design the harness around the stateless model. Persistent backends adapt *up* to it.

**Why this direction and not the other:**
- Faking persistent on top of stateless = "hold a list and resend it." `brain.py` already does exactly this (`memory` + `trim_memory()`). No new code.
- Faking stateless on top of persistent = writing session-emulation glue whose only job is to undo the persistence. Dead weight, and it couples the harness to one vendor.

So: **the harness owns the message list and trimming. Every backend implements one method** — given the full context, return the next turn's `content` + tool calls.

- **llama-server adapter:** trivial. That call *is* `/v1/chat/completions`.
- **Gemini Live adapter:** opens the session once, and per turn forwards only the *delta* by diffing against what it already sent. Context compression (`contextWindowCompression` sliding window) and session resumption (`GoAway` handling) live **entirely inside this adapter**, invisible to the brain loop.

One abstraction, pluggable backends. No env-var fork inside the loop — just a different adapter object selected at startup.

```
brain loop  ─────────────►  Backend (interface)
(owns memory,                  │
 trim, frame buffer)           ├── LlamaServerBackend   → /v1/chat/completions (stateless, native)
                               ├── GeminiLiveBackend     → WebSocket session, sends deltas, hides compression
                               └── QwenOmniBackend        → WebSocket session (no native compression)
```

The brain loop hands over full context every turn and does not know or care whether the backend resends it or diffs it. That's the whole trick.

---

## Cloud model options

### Gemini 3.1 Flash Live Preview — primary choice
- Persistent WebSocket, native video at ~1 FPS, tool calling works.
- Rates: **$0.75 / 1M input**, **$4.50 / 1M output**. Frames ≈ 258 tokens each at default media resolution.
- `thinkingLevel`: `minimal` | `low` | `medium` | `high`. **Defaults to High if unset** — set `minimal` explicitly (it's the "thinking off" equivalent, lowest latency/cost). Thinking tokens bill as output tokens. Map `medium`/`high` to struggle-escalation only.
- **Built-in `contextWindowCompression`** (sliding window + trigger threshold) → unlimited session duration. **Session resumption** + `GoAway` warning handles the ~10-min connection reset transparently.
- Known bug: `gemini-3.1-flash-live-preview` has issues with `generate_reply()` / `update_instructions()` / `update_chat_ctx()` under LiveKit. Basic voice + tool calling + audio I/O work. If those break, fall back to Gemini 2.5 Flash Live.

### Qwen3.5-Omni-Flash-Realtime — viable cheaper alternative
- Rates: **$0.55 / 1M input (text+image)**, **$3.30 / 1M output**. Cheaper than Gemini on both.
- **90-day free tier: 1M input + 1M output tokens.** Good for a zero-cost first experiment.
- WebSocket session up to 120 min. Video: 50 turns / 120s of frames retained (sliding — oldest dropped, keeps running).
- Tool calling with video **works**. The "web search and tool calling are mutually exclusive" limitation only matters if you enable web search — Chotu doesn't. Irrelevant here.
- **No documented context compression or session resumption** — that's the one structural gap vs Gemini.

### Cost reference (5-min maze-style run, persistent/delta model)
~100 turns, ~824 tokens input delta/turn (3 frames + heartbeat text), ~80 tokens output/turn, ~2,000 tokens session open.
- Gemini Live: **~$0.10**
- Qwen Flash Realtime: **~$0.07**
- Always-on 8h/day on Gemini ≈ **~$10/month**.

(Earlier $0.28 estimate was wrong — it assumed stateless resend every turn. Persistent sends deltas, so it's ~half.)

---

## Video feed wiring (applies to BOTH cloud and local)

The Pi camera supports MJPEG streaming (libcamera / picamera2), so a real stream is available — not just on-demand stills.

Add to the **harness** (shared by all backends):

1. **Frame buffer** — `collections.deque(maxlen=N)` of recent base64 JPEG frames. A background task samples the Pi MJPEG stream at **1 FPS** into it. N≈3 (≈3s buffer ≈ one average move call's duration, so the model sees where it's been and where it's going).
2. **Multi-image message format** — attach the N buffered frames in one user message (array of `image_url` blocks), not one-per-turn like the old `capture_vision`.
3. **Timestamp hint** — prepend a short text note to the frame block: `"3 frames, ~1s apart, oldest first."` Otherwise the model reads them as unrelated photos, not motion.
4. **Image-aware token accounting** — `_estimate_tokens` in `brain.py` currently counts text only. It must add ~258 tokens/frame or `trim_memory` will silently blow the real `MEMORY_TOKEN_BUDGET`.

For cloud, the adapter forwards new frames as they arrive into the session. For local, the buffer contents attach to each turn's message.

---

## What local Qwen (llama-server) needs to "function the same way"

Local stays on `llama.cpp` / `llama-server` (current stack). Because the harness is stateless-first, local needs **no session machinery at all**. The entire delta vs cloud is:

- The shared frame buffer (above) — already built for cloud, reused.
- Multi-image attach in the message (above).
- Image token accounting (above).
- Timestamp hint (above).

**The one real unknown to test before committing:** does the specific Qwen-VL GGUF + llama-server build accept **3 images in a single call** without falling over? Some quantized vision models choke past 1–2 images. This is a ~20-minute test and it gates the whole local-video approach. Everything else is mechanical.

No WebSocket, no session object, no resumption — llama-server is stateless and the brain loop is already built for that.

---

## Out of scope for this change (noted so you don't pull them in)
- **Gemini Robotics-ER 1.6** — embodied-reasoning VLM that returns object coordinates. Future `get_perception` backend, **not** the brain. File away.
- Reasoning quality of small local models — known limitation, not what this work addresses.
- Maze skill specifics, OLED face, HUD overlay — separate threads.

---

## Definition of done for the cloud-brain milestone
1. `GeminiLiveBackend` adapter implementing the single backend method, selected at startup alongside the existing local adapter.
2. Harness frame buffer + 1 FPS Pi MJPEG sampler feeding frames to the active backend.
3. `_estimate_tokens` counts image tokens.
4. `thinkingLevel=minimal` default; compression + resumption inside the adapter.
5. Chotu runs the live loop with continuous visual context — moving while actually *seeing* where it's going — end to end on Gemini Live.
6. (Stretch) Same loop runs on `QwenOmniBackend` using the free tier, to validate the abstraction holds across two persistent vendors.
