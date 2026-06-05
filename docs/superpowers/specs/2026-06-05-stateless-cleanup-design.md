# Stateless Cleanup — design

**Date:** 2026-06-05
**Status:** approved, executing

## Why

The live/realtime pivot (Qwen-Omni Realtime + Gemini Live, persistent WebSocket
sessions, continuous frame streaming) was built and tested. Conclusion from
testing: PiCrawler motions take 2–3 s each, so sub-100-ms latency buys nothing.
Realtime APIs are voice-agent infrastructure Chotu does not need.

We are reverting to the **stateless turn-based brain** (one request/response per
turn) — which already exists and works — and pointing it at cloud Qwen models
via DashScope's OpenAI-compatible endpoint, in addition to the local
llama-server. This lets us test across the ~84 DashScope models (1M tokens each)
and the local model through one code path.

The realtime work stays in git history (`live-brain` branch + prior commits);
we delete it from the working tree.

## Scope (decisions locked with user)

1. **Delete all live/realtime code** from the working tree.
2. **Cloud Qwen via config** — reuse the existing `local` (OpenAI-compatible)
   provider by pointing env at DashScope. No new provider code.
3. **Collapse to a single prompt** — drop the mode-overlay system; one stateless
   system prompt.
4. **Fix MD↔code tool drift only** — make `PALIV.md` match the 13 tools actually
   in `tools.py`. Tool *trimming/merging* is a deliberate later pass, not now.

**No regression:** the stateless brain (`core/brain.py`) and its tests must keep
working. Baseline = `pytest` green before and after (minus the deleted tests).

## A. Delete live/realtime code

Delete these files (all live-only):

- `core/backend.py` — Backend Protocol + event dataclasses (live abstraction)
- `core/brain_live.py` — live producer/consumer loop
- `core/qwen_omni_backend.py` — Qwen-Omni Realtime WebSocket backend
- `core/gemini_live_backend.py` — Gemini Live backend
- `core/llama_backend.py` — adapter wrapping LLMClient into the live Backend Protocol
- `core/frame_sampler.py` — MJPEG 1 FPS sampler for live streaming
- `tests/test_backend_protocol.py`
- `tests/test_llama_backend.py`
- `tests/test_frame_sampler.py`
- `tests/test_qwen_callback_bridge.py`

**Keep** (used by the stateless brain — NOT live-only):

- `core/motion_lock.py` — imported by `core/brain.py:201` and `core/tools.py:20`.
- `tests/test_motion_lock.py`

## B. Collapse the mode system

- Append the heartbeat-rhythm content of `CHOTU_STATELESS.md` into
  `CHOTU_BASE.md` (as a "Heartbeat rhythm" section). Persona + rhythm become one
  file since there is only one mode.
- Delete `CHOTU_STATELESS.md` and `CHOTU_LIVE.md`.
- Rewrite `core/prompts.py`: remove `load_system_prompt(mode)` and the
  `PALIV_BRAIN_MODE` branch. New body:
  `SYSTEM_PROMPT = PALIV.md + "\n\n" + CHOTU_BASE.md`.
- Update `core/brain.py` module docstring (lines 1–6) to drop the
  mode-overlay description.

## C. Remove live wiring left in stateless files

- `core/tools.py`:
  - `capture_vision_tool(pi, frame_sampler=None)` → `capture_vision_tool(pi)`;
    drop the `frame_sampler.latest()` branch, always fetch from the Pi.
  - `build_dispatch(...)` → remove the `frame_sampler` parameter and its
    docstring line; `capture_vision` dispatch becomes
    `lambda **kw: capture_vision_tool(pi)`.
- `core/brain.py`:
  - Remove `_frame_sampler_ref` (line 204, unused dead state).
  - Update the `build_dispatch(...)` call (line 206–207) to drop
    `frame_sampler=None`.
  - Fix the stale comment (lines 199–200) referencing "future live-mode
    FrameSampler".

## D. Fix MD↔code tool drift

`PALIV.md` "## Tools" currently lists `log`, `face`, `lumos` (none exist as
tools) and omits real ones. Replace the list with the actual 13 tools from
`tools.py`, descriptions kept terse (one line each):

- `move(direction, steps, speed)` — walk/turn. forward/backward/turn left/turn right.
- `pose(name, speed)` — named pose. stand/sit static; wave/push up/look up/down/left/right animated.
- `set_legs(legs, speed)` — low-level: four [x,y,z] leg coords in mm.
- `do_trick(name, speed)` — pre-choreographed routine: pushup/twist/swimming/handwork.
- `set_face(name)` — OLED expression (idle, playful, greeting, sleeping, …).
- `speak(text)` — say one short line aloud. Max 1/turn, ≤15 words.
- `get_distance()` — ultrasonic, cm.
- `get_battery()` — voltage + percent.
- `capture_vision()` — forward-camera photo, deferred multimodal user-message after tool results.
- `get_perception(color, face, human)` — Vilib always-on CV; returns detection + x/y in 320×240 frame.
- `cast_spell(name)` — wand pose + room-light control (lumos/nox/avada_kedavra, per enabled set).
- `wait(seconds, reason)` — deliberate pause; records a memory entry.
- `explore(reason)` — blocking mapping subagent (30–120 s); builds world map.

Keep the budgets/speech/interrupt sections of `PALIV.md` as-is.

> Note: this is a *documentation accuracy* fix only. Deciding which of these to
> cut/merge (e.g. set_legs, the three sensing tools, explore) is a separate
> later pass, per the user.

## E. Cloud Qwen via config (.env.example + docs)

Rewrite the live-brain block of `.env.example`. Remove:
`PALIV_BRAIN_MODE`, `GEMINI_API_KEY`, `PALIV_GEMINI_MODEL`, `DASHSCOPE_API_KEY`
(realtime), `PALIV_QWEN_OMNI_WS_URL`, `PALIV_QWEN_OMNI_MODEL`,
`PALIV_LIVE_PROVIDER`.

Add a documented "cloud Qwen via DashScope (OpenAI-compatible)" example showing
the local provider repointed:

```
# Local llama-server (default)
PALIV_BRAIN_URL=http://localhost:8080/v1
PALIV_BRAIN_KEY=not-needed
PALIV_BRAIN_MODEL=Qwen3.5-4B-Q4_K_M.gguf

# --- OR --- cloud Qwen via DashScope (same OpenAI-compatible code path)
# PALIV_BRAIN_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
# PALIV_BRAIN_KEY=sk-...            # your DashScope API key
# PALIV_BRAIN_MODEL=qwen-vl-max     # any of the DashScope models
```

Update `README.md` and `CLAUDE.md`:
- Remove live-mode / `PALIV_BRAIN_MODE` / realtime references.
- `CLAUDE.md` "Authoritative docs": drop `CHOTU_STATELESS.md` / `CHOTU_LIVE.md`;
  system prompt is now `PALIV.md + CHOTU_BASE.md`.
- Document the cloud-Qwen-via-config option (one line under Stack).

## F. Doc cruft — out of scope for this change

Untracked root working notes (`CHAT.md`, `CHOTU_CLOUD_BRAIN_HANDOFF.md`,
`PALIV_CC_CONTEXT.md`, `SHOOT_BRIEF.md`) and historical live plan/spec docs under
`docs/superpowers/` are **left untouched** here — deleting untracked files is
lossy and history docs are a record. Can be a follow-up if wanted.

## Verification

- `pytest` green (the 4 deleted live tests removed; everything else passes).
- `python -c "from core.prompts import SYSTEM_PROMPT; print(len(SYSTEM_PROMPT))"`
  works and contains base + heartbeat content.
- `python -c "from core.tools import build_dispatch"` imports with no
  `frame_sampler` references.
- `grep -rn "frame_sampler\|brain_live\|PALIV_BRAIN_MODE\|load_system_prompt"
  core` returns nothing (except intentional history docs).
- `python -m scripts.dry_run "walk forward"` still runs (offline dry-run path).
