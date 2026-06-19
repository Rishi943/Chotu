# CLAUDE.md

Guidance for Claude Code when working in this repo.

## Behavioral guidelines

Reduce common LLM coding mistakes. Bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think before coding
Don't assume. Don't hide confusion. Surface tradeoffs.
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity first
Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

### 3. Surgical changes
Touch only what you must. Clean up only your own mess.
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove imports/variables/functions that YOUR changes orphaned. Don't remove pre-existing dead code unless asked.
- Every changed line should trace directly to the user's request.

### 4. Goal-driven execution
Define success criteria. Loop until verified.
- "Add validation" → "Write tests for invalid inputs, then make them pass."
- "Fix the bug" → "Write a test that reproduces it, then make it pass."
- "Refactor X" → "Ensure tests pass before and after."

For multi-step tasks, state a brief plan with a verify step per item. Strong criteria let you loop independently; weak ones ("make it work") require constant clarification.

### 5. Token economy
CLAUDE.md loads every session — keep output and process frugal.
- End summary = one paragraph (expand only if asked). Specs/plans/comments carry only load-bearing info.
- Don't re-read a file to confirm an edit landed (Edit/Write errors if it didn't).
- Prefer one targeted check over broad runs. Batch clarifying questions into one round.

## Project

PALIV is an open agent framework for always-on embodied robot pets. Chotu (SunFounder PiCrawler quadruped) is the first instance. Brain runs on a Linux laptop over LAN to a Pi 5 bridge.

## Authoritative docs

- **`PALIV.md`** — framework contract: loop model, tool budgets, hard interrupts, speech contract, tool definitions. Loaded into every system prompt.
- **`CHOTU_BASE.md`** — Chotu's persona (voice, personality table, examples, physical constraints) plus the heartbeat-rhythm rules.
- **`docs/superpowers/specs/`** — design specs for in-flight refactors (gitignored except when force-added).

The system prompt at runtime is `PALIV.md + CHOTU_BASE.md`, composed by `core/prompts.py`. The brain is stateless turn-based only (the live/realtime backends were removed — see git history / the `live-brain` branch).

## Stack

- Python 3.12 on both sides. No LangChain, no Pydantic AI — custom async tool loop.
- Brain LLM: `llama-server` (llama.cpp), OpenAI-compatible, port 8080. Default model `Qwen3.5-4B-Q4_K_M.gguf` (multimodal). Cloud Qwen via DashScope's OpenAI-compatible endpoint: point `PALIV_BRAIN_URL`/`PALIV_BRAIN_KEY`/`PALIV_BRAIN_MODEL` at DashScope (same `local` code path, no new provider). Claude fallback: `claude-sonnet-4-6` via `PALIV_LLM_PROVIDER=claude`.
- Pi bridge: FastAPI + uvicorn on port 7000, started with `sudo` (GPIO).
- HTTP client (laptop→Pi): `httpx` async (not `requests`).
- TTS: `piper` on laptop, played via `sounddevice`. 22050 Hz native, 100 ms silence pad. Phonetic substitution: `Chotu` → `Chaw-too`.
- Voice input: `openWakeWord` (hey_jarvis placeholder) + `faster-whisper` (small, CPU int8).

## Dev setup

Venvs (`.venv/` laptop, `~/chotu-bridge/.venv` Pi), Pi access (SSH `chotu.local`), and all run/start commands + env flags: see `docs/DEV.md`.

## Code layout

| Path | Side | Purpose |
|---|---|---|
| `core/brain.py` | laptop | Live loop, memory, terminal/voice input, tool dispatch |
| `core/prompts.py` | laptop | Composes PALIV.md + CHOTU_BASE.md as `SYSTEM_PROMPT` |
| `core/heartbeat.py` | laptop | Heartbeat scheduler + tool-chain guard |
| `core/events.py` | laptop | Event injectors (wake_word, battery_low, stop_word) |
| `core/habits.py` | laptop | Habit-tool bodies (placeholder; will hold investigate/explore once workflow sub-agent lands) |
| `core/pi_client.py` | laptop | Async httpx wrapper for every Pi bridge endpoint |
| `core/tools.py` | laptop | OpenAI tool schemas + dispatch map + `capture_vision_tool` |
| `core/spells.py` | laptop | Spell implementations (wand pose + soundbite + Tuya/HA call) |
| `core/voice.py` | laptop | Wake word + Whisper STT; `record_push_to_talk()` for one-shot PTT capture |
| `core/gui_server.py` | laptop | Browser GUI (FastAPI + SSE); `/ptt`, `/handsfree`, `/api/config` endpoints |
| `core/llm_client.py` | laptop | LLM provider abstraction (local llama / Anthropic) |
| `pi_bridge/server.py` | Pi | FastAPI bridge — `/move`, `/pose`, `/set_legs`, `/trick`, `/distance`, `/capture`, `/battery`, `/perception`, `/face`, `/health`, `/speak` |

The Pi-side `pi_bridge/chotu/` (face.py) is a separate package shipped to the Pi alongside `server.py` — do not rename it; it has nothing to do with the laptop `chotu`→`core` rename.

## Conventions

- **Standard Pi response envelope:** `{ "ok": true, "tool": "...", "result": {...}, "duration_ms": N, "timestamp": N, "error": null }`
- **Speech is a tool.** `speak(text)` is a registered tool; `brain.py` dispatches it like any other tool. The LLM's `content` field is the inner monologue — visible in the transcript but not spoken aloud.
- Failure modes: Pi unreachable → error envelope returned to LLM (no crash). LLM unreachable → log, no crash. Tool errors → error string returned to LLM.

## Hardware quirks

- **Two power rails.** Pi 5 USB-C feeds *only* the Pi. Servos draw from the 2S LiPo via `robot_hat`. If tricks brown out at full speed, the HAT charge port needs power — plugging the Pi alone won't help.
- **Trick speed cap = 100** (`MAX_TRICK_SPEED` in `pi_bridge/server.py`). Matches official PiCrawler examples; works on a charged servo rail. Used by trick poses (twist/swimming/handwork). `MAX_MOTION_SPEED=60` still applies to `move`. Static/animated poses cap at `MAX_POSE_SPEED=40`.
- **Pose covers tricks.** twist/swimming/handwork are routed through `/pose` and dispatched by the bridge's `_TRICKS` table at `MAX_TRICK_SPEED`. There is no `do_trick` brain tool — `/trick` still exists on the bridge for legacy/debug but the brain only calls `/pose`.
- **Startup pose.** Bridge `lifespan` does `crawler.do_step("stand", 40)` + 1.0s settle before serving. All habits assume start = stand and end with `crawler.do_step("stand", 40)`.
- **Bridge-died signature.** A `/pose` (or `/trick`) envelope returning `duration_ms < 100` for a trick-pose name means the bridge process crashed (likely brownout) and is replying with a cached/stale ok. Real trick poses take 5–10s.

## Known LLM quirks (Qwen3.5 + llama-server)

- **Model name**: must match exactly — set `PALIV_BRAIN_MODEL` in `.env`.
- **Thinking mode**: Qwen3.5 generates `<think>` blocks by default. Always disable with `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`.
- **Assistant message serialisation**: strip `None`-valued fields from `model_dump()` before re-sending — some builds reject them.
- **Vision**: for `capture_vision`, inject the JPEG as a multimodal `user` message deferred until AFTER all tool results in the same turn — otherwise an invalid tool→user→tool sequence breaks llama-server.

## Rules

- **NEVER run anything against a cloud model (DashScope/Qwen cloud, Claude, Gemini, any paid API) without explicit per-instance approval — including tests, probes, dry-runs, and one-off scripts.** This spends real tokens/quota. Ask first, every time, even for "just a quick test." Local llama-server (port 8080) is free and needs no approval. When a task seems to need a cloud call, propose the exact command and wait for an approve/deny.
- Don't add frameworks not listed in PALIV.md without asking.
- Don't design around the ReSpeaker mic (not yet ordered) — voice input uses laptop default mic.
- Don't train "hey chotu" wake word until the pipeline is verified end-to-end with hey_jarvis.
- Don't add SQLite/persistence until a later phase.
- Cloud LLMs (Claude, Gemini) are fallback only, never default.
- Don't rename `pi_bridge/chotu/` — it is Pi runtime code shipped separately.
- **Execution-mode recommendation must be per-task, not defaulted.** When presenting the writing-plans/executing-plans handoff options (subagent-driven vs. inline), do NOT auto-recommend subagent-driven just because the skill text says "(recommended)". Judge each task on cost and risk: recommend **inline** for single-file or tightly-coupled, sequential work, or when verification is gated on the user anyway; recommend **subagent-driven** only when tasks are genuinely independent/parallelizable or context-heavy enough that fresh per-task context pays off. State the reasoning (monetary/risk) for the pick.
