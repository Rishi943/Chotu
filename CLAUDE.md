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
- TTS: `espeak` via `robot_hat` on Pi

## Development Setup

- Laptop venv at project root: `.venv/`
- Pi has a separate venv at `~/chotu-bridge/.venv` (created with `--system-site-packages`)
- Pi IP: `192.168.0.190` (DHCP — confirm with `ssh chotu@chotu.local 'ip addr show wlan0'` if unreachable)
- Pi hostname resolves as `chotu.local` via mDNS when Avahi is working; fall back to IP in `.env`
- Pi access is SSH only
- Start Pi bridge: `ssh chotu@chotu.local` then `sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py`
- Start brain: `source .venv/bin/activate && python3 -m chotu.brain`
- Debug mode: `CHOTU_DEBUG=1 python3 -m chotu.brain`
- Start llama-server first: `llama-server -m /home/rishi/.local/share/localis/models/Qwen3.5-4B-Q4_K_M.gguf --port 8080 -ngl 99`

## Laptop Code (`chotu/`)

| File | Purpose |
|---|---|
| `brain.py` | Main agent loop — LLM tool call cycle, memory buffer, terminal input |
| `pi_client.py` | Async httpx wrapper for every Pi bridge endpoint |
| `tools.py` | OpenAI tool schemas + dispatch map + `capture_vision_tool` |
| `system_prompt.py` | Chotu's personality, speech rules, tool docs, examples |

## Pi Bridge (`~/chotu-bridge/server.py`)

Single-file FastAPI server. Endpoints: `/move`, `/pose`, `/speak`, `/distance`, `/capture`, `/battery`, `/health`. All return the standard envelope.

**`/dance` was removed** — `do_action("dance")` runs an infinite loop on the PiCrawler and cannot be reliably bounded.

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

## Tools (6 active)

| Tool | Side | Notes |
|---|---|---|
| `move(direction, steps, speed)` | Pi | direction: forward/backward/turn left/turn right |
| `pose(name)` | Pi | stand/sit/wave/push up/look up/look down/look left/look right |
| `speak(text)` | Pi | espeak TTS, broken English only |
| `get_distance()` | Pi | ultrasonic, returns cm |
| `get_battery()` | Pi | voltage + percent |
| `capture_vision()` | Pi+laptop | Pi captures JPEG; laptop injects it into LLM context |
| `wait(seconds, reason)` | laptop | local asyncio.sleep |

## Phase Status

- **Phase 1 (complete):** Text chat → LLM → physical movement. Pi bridge, laptop agent, tool dispatch, system prompt, end-to-end wiring.
- **Next — Task 10:** Obstacle reflex — asyncio task polling `get_distance()` every 200ms, `estop` event blocks movement tools when obstacle < threshold.
- **Next — Task 11:** Mode B heartbeat — 5-second autonomous tick, vilib tag events fed into brain.

## Rules

- Do not add frameworks not listed in CHOTU.md without asking
- Do not design around the ReSpeaker mic (not yet ordered)
- Do not add SQLite/persistence until a later phase
- Cloud LLMs (Claude, Gemini) are fallback only, never default
