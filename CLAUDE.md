# CLAUDE.md

Guidance for Claude Code when working in this repo.

## Project

PALIV is an open agent framework for always-on embodied robot pets. Chotu (SunFounder PiCrawler quadruped) is the first instance. Brain runs on a Linux laptop over LAN to a Pi 5 bridge.

## Authoritative docs

- **`PALIV.md`** — framework contract: loop model, tool budgets, hard interrupts, speech contract, tool definitions. Loaded into every system prompt.
- **`CHOTU_BASE.md`** — Chotu's persona: voice, personality probability table, examples, physical constraints. Shared across all modes.
- **`CHOTU_STATELESS.md`** — heartbeat-rhythm rules. Loaded when `PALIV_BRAIN_MODE=stateless` (default).
- **`CHOTU_LIVE.md`** — continuous-reactivity rules for the persistent backend. Loaded when `PALIV_BRAIN_MODE=live`.
- **`PALIV_CC_CONTEXT.md`** — pivot context, useful while v1 lands. Drop after the state machine ships.
- **`docs/superpowers/specs/`** — design specs for in-flight refactors (gitignored except when force-added).

The system prompt at runtime is `PALIV.md + CHOTU_BASE.md + CHOTU_{MODE}.md`, composed by `core/prompts.py` based on `PALIV_BRAIN_MODE`.

## Stack

- Python 3.12 on both sides. No LangChain, no Pydantic AI — custom async tool loop.
- Brain LLM: `llama-server` (llama.cpp), OpenAI-compatible, port 8080. Default model `Qwen3.5-4B-Q4_K_M.gguf` (multimodal). Cloud fallback: `claude-sonnet-4-6` via `PALIV_LLM_PROVIDER=claude`.
- Pi bridge: FastAPI + uvicorn on port 7000, started with `sudo` (GPIO).
- HTTP client (laptop→Pi): `httpx` async (not `requests`).
- TTS: `piper` on laptop, played via `sounddevice`. 22050 Hz native, 100 ms silence pad. Phonetic substitution: `Chotu` → `Chaw-too`.
- Voice input: `openWakeWord` (hey_jarvis placeholder) + `faster-whisper` (small, CPU int8).

## Dev setup

- Laptop venv: `.venv/` at project root.
- Pi venv: `~/chotu-bridge/.venv` (created with `--system-site-packages`).
- Pi access: SSH only. Hostname `chotu.local` via mDNS, fallback to IP in `.env`.
- Start llama-server: `llama-server -m <model.gguf> --mmproj <mmproj.gguf> --port 8080 -ngl 99 -c 32768 --parallel 1`
- Start Pi bridge: `ssh chotu@chotu.local 'sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py'`
- Start brain: `source .venv/bin/activate && python3 -m core.brain`
- Voice input: `PALIV_VOICE=1 python3 -m core.brain`
- Debug logging: `PALIV_DEBUG=1`
- Mute audio: `PALIV_MUTE=1`
- Offline dry-run: `python -m scripts.dry_run "walk forward"` (real LLM, faked Pi)

## Code layout

| Path | Side | Purpose |
|---|---|---|
| `core/brain.py` | laptop | Live loop, memory, terminal/voice input, tool dispatch |
| `core/prompts.py` | laptop | Composes PALIV.md + CHOTU_BASE.md + mode overlay (CHOTU_STATELESS.md or CHOTU_LIVE.md) as `SYSTEM_PROMPT` |
| `core/heartbeat.py` | laptop | Heartbeat scheduler + tool-chain guard |
| `core/events.py` | laptop | Event injectors (wake_word, battery_low, stop_word) |
| `core/habits.py` | laptop | Habit-tool bodies (placeholder; will hold investigate/explore once workflow sub-agent lands) |
| `core/pi_client.py` | laptop | Async httpx wrapper for every Pi bridge endpoint |
| `core/tools.py` | laptop | OpenAI tool schemas + dispatch map + `capture_vision_tool` |
| `core/spells.py` | laptop | Spell implementations (wand pose + soundbite + Tuya/HA call) |
| `core/voice.py` | laptop | Wake word + Whisper STT |
| `core/gui_server.py` | laptop | Browser GUI (FastAPI + SSE) |
| `core/llm_client.py` | laptop | LLM provider abstraction (local llama / Anthropic) |
| `pi_bridge/server.py` | Pi | FastAPI bridge — `/move`, `/pose`, `/set_legs`, `/trick`, `/distance`, `/capture`, `/battery`, `/perception`, `/face`, `/health`, `/speak` |

The Pi-side `pi_bridge/chotu/` (face.py) is a separate package shipped to the Pi alongside `server.py` — do not rename it; it has nothing to do with the laptop `chotu`→`core` rename.

## Conventions

- **Standard Pi response envelope:** `{ "ok": true, "tool": "...", "result": {...}, "duration_ms": N, "timestamp": N, "error": null }`
- **Speech is a tool.** `speak(text)` is a registered tool; `brain.py` dispatches it like any other tool. The LLM's `content` field is the inner monologue — visible in the transcript but not spoken aloud.
- Failure modes: Pi unreachable → error envelope returned to LLM (no crash). LLM unreachable → log, no crash. Tool errors → error string returned to LLM.

## Hardware quirks

- **Two power rails.** Pi 5 USB-C feeds *only* the Pi. Servos draw from the 2S LiPo via `robot_hat`. If tricks brown out at full speed, the HAT charge port needs power — plugging the Pi alone won't help.
- **Trick speed cap = 100** (`MAX_TRICK_SPEED` in `pi_bridge/server.py`). Matches official PiCrawler examples; works on a charged servo rail. `MAX_MOTION_SPEED=60` still applies to `move`/`set_legs`.
- **Startup pose.** Bridge `lifespan` does `crawler.do_step("stand", 40)` + 1.0s settle before serving. All habits assume start = stand and end with `crawler.do_step("stand", 40)`.
- **Bridge-died signature.** A `/trick` envelope returning `duration_ms < 100` means the bridge process crashed (likely brownout) and is replying with a cached/stale ok. Real tricks take 5–10s.

## Known LLM quirks (Qwen3.5 + llama-server)

- **Model name**: must match exactly — set `PALIV_BRAIN_MODEL` in `.env`.
- **Thinking mode**: Qwen3.5 generates `<think>` blocks by default. Always disable with `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`.
- **Assistant message serialisation**: strip `None`-valued fields from `model_dump()` before re-sending — some builds reject them.
- **Vision**: for `capture_vision`, inject the JPEG as a multimodal `user` message deferred until AFTER all tool results in the same turn — otherwise an invalid tool→user→tool sequence breaks llama-server.

## Rules

- Don't add frameworks not listed in PALIV.md without asking.
- Don't design around the ReSpeaker mic (not yet ordered) — voice input uses laptop default mic.
- Don't train "hey chotu" wake word until the pipeline is verified end-to-end with hey_jarvis.
- Don't add SQLite/persistence until a later phase.
- Cloud LLMs (Claude, Gemini) are fallback only, never default.
- Don't rename `pi_bridge/chotu/` — it is Pi runtime code shipped separately.
