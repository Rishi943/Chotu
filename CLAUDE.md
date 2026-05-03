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
- TTS: `piper` on laptop, played via `sounddevice`. Pi `/speak` endpoint exists but is unused — Pi speaker is too quiet. Voice model: `chotu/voices/en_GB-northern_english_male-medium.onnx` (path in `LOCALIS_PIPER_MODEL`). 22050 Hz native, 100ms silence pad at start to prevent clipping. Phonetic substitution: `Chotu` → `Chaw-too` so piper pronounces it correctly.
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
| `spells.py` | Spell implementations — wand pose + soundbite + HA REST call |
| `system_prompt.py` | Chotu's personality, speech rules, tool docs, examples |
| `voice.py` | Wake word detection (openWakeWord) + Whisper STT; enabled via `CHOTU_VOICE=1` |

## Pi Bridge (`~/chotu-bridge/server.py`)

Single-file FastAPI server. Endpoints: `/move`, `/pose`, `/set_legs`, `/speak`, `/distance`, `/capture`, `/battery`, `/health`. All return the standard envelope.

**`/dance` was removed** — `do_action("dance")` runs an infinite loop on the PiCrawler and cannot be reliably bounded.

**`/set_legs`** — takes `{legs: [[x,y,z]×4], speed}` and calls `crawler.do_step(legs, speed)` in a thread executor. Lets the LLM invent custom poses and gaits frame-by-frame.

## Conventions

- Standard response envelope from Pi: `{ "ok": true, "tool": "...", "result": {...}, "duration_ms": N, "timestamp": N, "error": null }`
- **Speech is not a tool.** What Chotu says aloud is the LLM's `message.content` — `brain.py` parses it and fires `local_speak()` as `asyncio.create_task` (parallel with tool dispatch). Empty content = silent turn. See "Known LLM Quirks" for why.
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
- **Speak as a tool fails on small models**: Earlier `speak()` was a function-call tool. Qwen3.5-4B reliably called physical tools (move, get_distance, etc.) but wrote `speak("...")` as plain text in `content` instead of as a tool call (5 of 8 dry_run prompts failed). The model pattern-matches example formatting and treats anything that looks like text in examples as text output. Fix: emit speech via `message.content` and fire piper directly from brain. Examples in the prompt show only the spoken line — never `function_name(...)` syntax, parenthetical action notes, or stage directions.

## Tools

Reactive mode (`TOOL_SCHEMAS`):

| Tool | Side | Notes |
|---|---|---|
| `move(direction, steps, speed)` | Pi | direction: forward/backward/turn left/turn right |
| `pose(name, speed)` | Pi | stand/sit/wave/push up/look up/look down/look left/look right. **Default speed 50** — stand/sit move all 12 servos at once, high speed causes brown-outs. Pi bridge hard-caps at `MAX_POSE_SPEED=50`. |
| `set_legs(legs, speed)` | Pi | Four `[x,y,z]` coords. Neutral `[60,0,-30]`. z=height, x=reach, y=sideways. Leg indices: 0=FR, 1=FL, 2=BR, 3=BL. Chain calls for gaits. |
| `do_trick(name, speed)` | Pi | pushup / twist / swimming / handwork. Tricks self-manage current via sleeps between steps — speed 80 is fine. |
| `get_distance()` | Pi | ultrasonic, returns cm |
| `get_battery()` | Pi | voltage + percent |
| `capture_vision()` | Pi+laptop | Pi captures JPEG; laptop injects as deferred user-message after all tool results |
| `get_perception(color, face, human)` | Pi | Vilib always-on CV, returns detection + x/y |
| `wait(seconds, reason)` | laptop | local asyncio.sleep |
| `cast_spell(name)` | laptop+HA | Raises FR leg as wand, plays soundbite, calls HA REST API. Spells: lumos/nox/avada_kedavra. Controlled by `SPELLS_ENABLED` env var. |

Goal mode (`GOAL_TOOL_SCHEMAS = TOOL_SCHEMAS + GOAL_ONLY_SCHEMAS`) adds `goal_complete(outcome, success)`. Goal-mode-only tools live in `GOAL_ONLY_SCHEMAS` so they're hidden from the model in reactive mode (otherwise the model invents reasons to call them).

**Speech is not a tool.** The LLM's `message.content` is what Chotu says aloud — fired via `local_speak()` from brain.py.

**Parallel dispatch:** `brain.py` uses `asyncio.gather()` so multiple tool calls in one LLM turn fire concurrently. Speech via `_fire_speak_if_content()` is `asyncio.create_task` so it runs alongside tool dispatch (not awaited). `MAX_TOOL_ITERATIONS = 6` per user turn (matches `dry_run.py`); raise only if chained `set_legs` gaits need more frames.

## Personality (current state)

Chotu is a quadruped robot with a dignified, sardonic voice — dry, self-aware, occasionally delighted. Aware that it is a robot and references it situationally. Emotional palette includes a "dark side" (reluctant, grumpy compliance), curiosity breaks (drops the act when something genuinely interests it), a please mechanic (asks for "please" before performative requests), and proportional cursing (matches the human's register).

Examples in the system prompt show ONLY the spoken text — never function-call syntax, parenthetical action notes, or "(also calls X tool)" markers. The model copies whatever it sees in examples; anything that looks like a stage direction will leak into speech.

## Dry-run harness

`scripts/dry_run.py` — runs the real brain loop against llama-server but fakes every Pi response. Use when the Pi is unplugged/charging to evaluate personality and tool-call behavior in isolation.

```bash
python -m scripts.dry_run                       # interactive
python -m scripts.dry_run "walk and say hi"     # one-shot
```

Prints tool calls with args and `[speaks]` lines parsed from the LLM's `message.content` after each turn. No Pi traffic, no movement, no audio (piper is not invoked in dry_run).

## Spells + Home Assistant

`chotu/spells.py` implements three Harry Potter spells. Each: raises FR leg as wand (`[80,0,20]`) → plays soundbite → HA REST API call.

- **`lumos`** — `POST /api/services/light/turn_on`
- **`nox`** — `POST /api/services/light/turn_off`
- **`avada_kedavra`** — turn_on green (rgb=[0,255,0], brightness=255) → 300ms sleep → turn_off

HA config in `.env`: `HA_BASE_URL`, `HA_TOKEN`, `HA_LIGHT_ENTITY=light.rishi_room_light`

**Soundbites:** WAV files in `assets/spells/`. Paths in `.env` as `SPELL_LUMOS_SOUND`, `SPELL_NOX_SOUND`, `SPELL_AVADA_SOUND`. Played via sounddevice with TTS lock (serialized). Stereo WAVs must be reshaped to `(-1, 2)` before `sd.play()`.

**`SPELLS_ENABLED`** — comma-separated list of active spells (e.g. `avada_kedavra` for demo). Filters both the tool schema enum AND the description seen by the LLM. If only one spell is enabled the description instructs the model to always use it without asking.

## TTS / Audio

All audio (speech + soundbites) serializes through `_get_tts_lock()` in `tools.py`. Without the lock, concurrent `sd.play()` calls corrupt PortAudio state and segfault. `sd.stop()` is called before every `sd.play()`. Piper synthesis happens outside the lock so the next utterance renders in parallel with current playback.

`local_speak()` is in `tools.py`. `tools.py` calls `load_dotenv()` at import time so env vars are available when `TOOL_SCHEMAS` is built (brain.py imports tools before calling load_dotenv itself).

## Done

- Phase 1 — chat → LLM → movement, full end-to-end wiring
- Obstacle reflex — `obstacle_poller` polls distance every 200ms; `estop` event blocks movement tools at <15cm
- `set_legs` — per-leg coordinate control wired through Pi bridge, pi_client, tools schema, and dispatch
- Parallel tool dispatch in `brain.py` (tools + speech fire concurrently)
- Personality rewrite — sardonic/dignified voice, please mechanic, dark side, proportional cursing
- Speech tightened — 15-word max enforced via system prompt; examples rewritten to match
- Speech via message content — `speak` removed from tools; `local_speak()` fires from brain after parsing content. Reliable on small models.
- Local TTS — piper on laptop via sounddevice, `Chotu`→`Chaw-too` substitution, 100ms silence pad
- TTS lock — `_get_tts_lock()` serializes all audio; fixes concurrent segfault
- Pose speed cap — Pi bridge clamps pose speed to 50 (`MAX_POSE_SPEED`) to prevent brown-outs
- Mode-aware tool schemas — `GOAL_ONLY_SCHEMAS` (currently `goal_complete`) hidden in reactive mode
- Dry-run harness for offline prompt evaluation
- Voice input — wake word (hey_jarvis) + Whisper STT, enabled via `CHOTU_VOICE=1`
- Spells + HA — `cast_spell` tool, `chotu/spells.py`, HA REST integration, soundbite playback, `SPELLS_ENABLED` filter

## What to do next

- **Deploy Pi bridge** — `scp pi_bridge/server.py chotu@chotu.local:~/chotu-bridge/server.py` to get pose speed cap live
- **Train "hey chotu" wake word** — collect ~30 recordings, use openWakeWord training script. hey_jarvis is the current placeholder.
- **Runtime verification on real Pi** after charging — walk this checklist against the physical robot:
  - `"walk forward 3 steps then turn around"` — sequential `move` calls, speech in personality
  - `"sit down"` — pose + dry remark
  - `"dance like a worm"` — chained `set_legs` frames OR `do_trick`, stops cleanly
  - `"are you a robot?"` — sardonic acknowledgment, no broken English
  - `"is anyone in the room?"` — `get_perception(human=true)` + interpretation
  - `"fly to the moon"` — refuses with personality, no tool call
  - `"do some magic"` — `cast_spell(avada_kedavra)`, wand pose, soundbite, green flash
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
