# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Chotu is an embodied AI agent on a SunFounder PiCrawler quadruped robot. Read `CHOTU.md` for the full spec — it is the authoritative source for architecture, constraints, and phasing.

Key points:
- **Two-process split:** Laptop (brain, LLM, vision, TTS) ↔ Pi 5 (dumb FastAPI bridge for hardware). Always be explicit about which side code targets.
- **Phase 1 is complete.** Code exists in `chotu/` (laptop) and `~/chotu-bridge/` (Pi).

## Stack

- Python 3.12 on both sides
- **No LangChain, no Pydantic AI** — custom async tool loop (~30 lines)
- Brain LLM: `llama-server` (llama.cpp), OpenAI-compatible endpoint, local
  - Model: `Qwen3.5-4B-Q4_K_M.gguf` — **multimodal** (text + vision)
  - Port: `8080`, started manually before running the brain
  - Thinking mode must be disabled: pass `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` on every LLM call
- Pi bridge: FastAPI + uvicorn on port 7000, started with `sudo` (GPIO requirement)
- HTTP client (laptop→Pi): `httpx` async (not `requests`)
- TTS: `espeak` on Pi — runs in thread executor (non-blocking). **Do not add `capture_output=True`** — that silences the speaker by redirecting audio to a pipe.
- Voice input (laptop): `openWakeWord` (ONNX, hey_jarvis model) + `faster-whisper` (small, CPU int8). Wake word model at `~/Rishi/AI/Chotu/models/hey_jarvis_v0.1.onnx`. Set `CHOTU_MIC_DEVICE` to override default mic.

## Development Setup

- Laptop venv at project root: `.venv/`
- Pi has a separate venv at `~/chotu-bridge/.venv` (created with `--system-site-packages`)
- Pi IP: `192.168.0.190` (DHCP — confirm with `ssh chotu@chotu.local 'ip addr show wlan0'` if unreachable)
- Pi hostname resolves as `chotu.local` via mDNS when Avahi is working; fall back to IP in `.env`
- Pi access is SSH only
- Start Pi bridge: `ssh chotu@chotu.local` then `sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py`
- Start brain (terminal input): `source .venv/bin/activate && python3 -m chotu.brain`
- Start brain (goal mode): `source .venv/bin/activate && python3 -m chotu.brain --goal "your goal here"`
- Start brain (voice input): `CHOTU_VOICE=1 python3 -m chotu.brain`
- Debug mode: `CHOTU_DEBUG=1 python3 -m chotu.brain`
- Start llama-server first: `llama-server -m /home/rishi/.local/share/localis/models/Qwen3.5-4B-Q4_K_M.gguf --mmproj /home/rishi/.local/share/localis/models/mmproj-BF16.gguf --port 8080 -ngl 99 -c 16384 --parallel 1`

## Laptop Code (`chotu/`)

| File | Purpose |
|---|---|
| `brain.py` | Main agent loop — LLM tool call cycle, memory buffer, terminal/voice input |
| `pi_client.py` | Async httpx wrapper for every Pi bridge endpoint |
| `tools.py` | OpenAI tool schemas + dispatch map + `capture_vision_tool` |
| `system_prompt.py` | Chotu's personality, speech rules, tool docs, examples |
| `voice.py` | Wake word detection (openWakeWord) + Whisper STT; enabled via `CHOTU_VOICE=1` |

## Pi Bridge (`~/chotu-bridge/server.py`)

Single-file FastAPI server. Endpoints: `/move`, `/pose`, `/set_legs`, `/speak`, `/distance`, `/capture`, `/battery`, `/health`. All return the standard envelope.

**`/dance` was removed** — `do_action("dance")` runs an infinite loop on the PiCrawler and cannot be reliably bounded.

**`/set_legs`** — takes `{legs: [[x,y,z]×4], speed}` and calls `crawler.do_step(legs, speed)` in a thread executor. Lets the LLM invent custom poses and gaits frame-by-frame.

## Conventions

- Standard response envelope from Pi: `{ "ok": true, "tool": "...", "result": {...}, "duration_ms": N, "timestamp": N, "error": null }`
- Chotu's character voice (broken English, no articles, short sentences) applies only to spoken LLM output (`speak` tool). Inner monologue and terminal output use normal English.
- System prompt lives in `chotu/system_prompt.py`
- Failure modes: Pi unreachable → error envelope returned to LLM (no crash). LLM unreachable → log, no crash. Tool errors → error string returned to LLM.

## Pi Bridge Setup (after reflash)

Pi OS image must have SunFounder libs. Install order:
1. Flash Pi OS, set user=`chotu`, hostname=`chotu`, enable SSH+WiFi in Raspberry Pi Imager
2. `git clone -b v2.0 https://github.com/sunfounder/robot-hat.git --depth 1 && cd robot-hat && sudo python3 install.py`
3. `git clone -b v2.0 https://github.com/sunfounder/picrawler.git --depth 1 && cd picrawler && sudo python3 install.py`
4. `git clone https://github.com/sunfounder/vilib.git --depth 1 && cd vilib && sudo python3 install.py`
5. `sudo raspi-config nonint do_i2c 0`
6. `python3 -m venv --system-site-packages ~/chotu-bridge/.venv`
7. `~/chotu-bridge/.venv/bin/pip install fastapi "uvicorn[standard]"`
8. Deploy: `scp pi_bridge/server.py chotu@chotu.local:~/chotu-bridge/server.py`

Note: venv must use `--system-site-packages` so it can see picrawler/vilib/robot_hat.

## Known LLM Quirks (Qwen3.5 + llama-server)

- **Model name**: must match exactly — `Qwen3.5-4B-Q4_K_M.gguf` (set in `.env` as `CHOTU_BRAIN_MODEL`)
- **Thinking mode**: Qwen3.5 generates `<think>` blocks by default, consuming all tokens before producing output. Always disable with `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`
- **Assistant message serialisation**: strip `None`-valued fields from `model_dump()` before re-sending to llama-server — some builds reject them
- **Vision**: Qwen3.5 is multimodal. For `capture_vision`, the JPEG is injected as a multimodal `user` message deferred until AFTER all tool results in the same turn — inserting it mid-loop creates an invalid tool→user→tool sequence that llama-server rejects
- **system_prompt.py**: use `.replace("{mode_description}", ...)` not `.format()` — the template contains JSON examples with `{...}` that confuse str.format()

## Tools (8 active)

| Tool | Side | Notes |
|---|---|---|
| `move(direction, steps, speed)` | Pi | direction: forward/backward/turn left/turn right |
| `pose(name)` | Pi | stand/sit/wave/push up/look up/look down/look left/look right |
| `set_legs(legs, speed)` | Pi | Four `[x,y,z]` coords. Neutral `[60,0,-30]`. z=height, x=reach, y=sideways. Leg indices: 0=FR, 1=FL, 2=BR, 3=BL. Chain calls for gaits. |
| `speak(text)` | Pi | espeak TTS, Rocky broken English only |
| `get_distance()` | Pi | ultrasonic, returns cm |
| `get_battery()` | Pi | voltage + percent |
| `capture_vision()` | Pi+laptop | Pi captures JPEG; laptop injects as deferred user-message after all tool results |
| `wait(seconds, reason)` | laptop | local asyncio.sleep |

**Parallel dispatch:** `brain.py` uses `asyncio.gather()` so multiple tool calls in one LLM turn fire concurrently (e.g. `move` + `speak` run together). Order is preserved for message construction. `MAX_TOOL_ITERATIONS = 20` to leave room for chained `set_legs` gaits.

## Personality (current state)

Chotu is a small curious creature — not a robot, never self-labels. Voice modeled on Rocky from *Project Hail Mary*: no articles, short fragments, "question?" suffix, repetition ("amaze amaze amaze"), addresses people as "friend". Emotional range is restricted to four: curiosity, wonder, excitement, confusion. No grumpiness or sarcasm.

Key prompt sections (in `chotu/system_prompt.py`):
- Section 5 explicitly deflects "are you a robot?" — Chotu says "not know word" rather than confirming
- Section 7 documents the leg coordinate system for `set_legs`
- Section 10 **STOP RULES** — hard caps per request type (conversational = 1 speak, physical = 1 tool + 1 speak, gait = 4-6 frames total). Without these the model loops pathologically.

## Dry-run harness

`scripts/dry_run.py` — runs the real brain loop against llama-server but fakes every Pi response. Use when the Pi is unplugged/charging to evaluate personality and tool-call behavior in isolation.

```bash
python -m scripts.dry_run                       # interactive
python -m scripts.dry_run "walk and say hi"     # one-shot
```

Prints tool calls with args, speak text, and final inner monologue. No Pi traffic, no movement.

## Done

- Phase 1 — chat → LLM → movement, full end-to-end wiring
- Obstacle reflex — `obstacle_poller` polls distance every 200ms; `estop` event blocks movement tools at <15cm
- `set_legs` — per-leg coordinate control wired through Pi bridge, pi_client, tools schema, and dispatch
- Parallel tool dispatch in `brain.py` (move + speak fire concurrently)
- Personality rewrite — Rocky voice, creature identity, four-emotion range, explicit STOP rules
- Dry-run harness for offline prompt evaluation
- Voice input — wake word (hey_jarvis) + Whisper STT, enabled via `CHOTU_VOICE=1`
- speak bug fixed — removed `capture_output=True` from espeak subprocess; now runs in executor

## What to do next

- **Train "hey chotu" wake word** — collect ~30 recordings, use openWakeWord training script. hey_jarvis is the current placeholder.
- **Mode B heartbeat** — 5-second autonomous tick, vilib tag events fed into the brain loop as user-role messages. Currently Mode B is plumbed into the system prompt but no actual heartbeat task is running.
- **Runtime verification on real Pi** after charging — walk this checklist against the physical robot:
  - `"walk forward 2 steps and say hi"` — observe parallel `move` + `speak`
  - `"stretch"` — single `set_legs` frame
  - `"be a worm"` — 4-6 chained `set_legs` frames, stops cleanly
  - `"are you a robot?"` — deflects, does not self-label
  - `"how are you feeling?"` — single `speak`, no chain
- **Memory persistence** — current `memory` is a `deque(maxlen=15)` in-process; lost on restart. Phase gate says no SQLite yet, but a simple jsonl append log would survive restarts.
- **Token budget monitoring** — system prompt is ~9.3KB (~1450 words). Tool schemas add more. Log total prompt tokens per turn to catch context bloat.

## PiCrawler Physical Dimensions

- Body length: ~15cm (front to back)
- Weight: ~960g
- Legs: 4 × 3-servo legs (12 servos total), aluminum alloy frame
- Ultrasonic sensor: mounted front-center
- **Safe obstacle stop threshold: 15cm** — robot body length, gives ~0cm margin at trigger point; tune up if it clips obstacles

## Rules

- Do not add frameworks not listed in CHOTU.md without asking
- Do not design around the ReSpeaker mic (not yet ordered) — voice input currently uses laptop default mic
- Do not train "hey chotu" wake word until pipeline is verified working end-to-end with hey_jarvis
- Do not add SQLite/persistence until a later phase
- Cloud LLMs (Claude, Gemini) are fallback only, never default
