# Chotu

A small quadruped robot with too much going on upstairs.

Chotu is an LLM-driven embodied AI agent running on a [SunFounder PiCrawler](https://www.sunfounder.com/products/picrawler-robot-kit) — a 12-servo, four-legged robot. You talk to it (or type). It thinks. It moves, looks around, makes remarks, and occasionally complains. All inference runs locally.

---

## What it does

- **Talks back** — sardonic, dry, occasionally delighted. Speaks aloud via neural TTS.
- **Moves** — walks, turns, sits, waves, does push-ups, invents custom gaits frame-by-frame.
- **Sees** — takes camera snapshots and describes what it sees using a multimodal LLM.
- **Senses** — ultrasonic distance sensor stops movement automatically when something is in the way.
- **Listens** — wake word + Whisper STT so you can talk to it hands-free.
- **Pursues goals** — autonomous mode: give it a goal, it loops until done or gives up.
- **Does magic** — `cast_spell(avada_kedavra)` raises its leg like a wand and flashes the room light green. Yes, really.

---

## Architecture

Two processes, two machines:

```
Laptop                              Raspberry Pi 5
──────────────────────────────      ──────────────────────────────
LLM (llama-server, Qwen3.5-4B)  ←→  FastAPI bridge (port 7000)
Agent loop (brain.py)               PiCrawler HAL (servos, sensors)
Voice input (wake word + Whisper)   Camera (Vilib)
TTS (piper → sounddevice)           OLED face expressions
Browser GUI (FastAPI, port 8888)
```

The laptop is the brain. The Pi is a dumb HTTP server that executes commands and returns JSON. The LLM never touches the Pi directly — `brain.py` dispatches tool calls over LAN via async HTTP.

---

## Hardware

- **SunFounder PiCrawler** robot kit (comes with Pi hat, 12 servos, ultrasonic sensor, camera mount)
- **Raspberry Pi 5** (4GB+ recommended)
- **Laptop** with a decent GPU — tested with an RTX 3060 12GB. The 4B model fits easily; a CPU-only machine will work but be slow.
- A USB mic on the laptop if you want voice input (or any sounddevice-compatible mic)

---

## Installation

### Laptop side

**Prerequisites:** Python 3.12, [llama-server](https://github.com/ggml-org/llama.cpp) on PATH, [piper](https://github.com/rhasspy/piper) on PATH.

```bash
git clone <this-repo> && cd Paliv
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your paths:

```bash
cp .env.example .env
```

Key variables:

| Variable | What it does |
|---|---|
| `PI_HOST` | Pi bridge URL, e.g. `http://chotu.local:7000` |
| `PALIV_BRAIN_URL` | llama-server endpoint, default `http://localhost:8080/v1` |
| `PALIV_BRAIN_MODEL` | Model filename, e.g. `Qwen3.5-4B-Q4_K_M.gguf` |
| `LOCALIS_PIPER_MODEL` | Path to your `.onnx` piper voice model |
| `PALIV_VOICE` | Set to `1` to enable wake word + Whisper STT |
| `PALIV_WAKE_WORD_MODEL` | Path to your `.onnx` wake word model |

Download the model files:
- LLM: `Qwen3.5-4B-Q4_K_M.gguf` + `mmproj-BF16.gguf` (multimodal projector) from Hugging Face
- Piper voice: any piper `.onnx` + `.onnx.json` pair from [rhasspy/piper-voices](https://github.com/rhasspy/piper-voices)
- Wake word: `hey_jarvis_v0.1.onnx` from [dscripka/openWakeWord](https://github.com/dscripka/openWakeWord)

---

### Pi side

Flash Raspberry Pi OS, set hostname=`chotu`, user=`chotu`, enable SSH and WiFi in Raspberry Pi Imager. Then SSH in and run:

```bash
# SunFounder libraries (must be done in this order)
git clone -b v2.0 https://github.com/sunfounder/robot-hat.git --depth 1
cd robot-hat && sudo python3 install.py && cd ..

git clone -b v2.0 https://github.com/sunfounder/picrawler.git --depth 1
cd picrawler && sudo python3 install.py && cd ..

git clone https://github.com/sunfounder/vilib.git --depth 1
cd vilib && sudo python3 install.py && cd ..

sudo raspi-config nonint do_i2c 0

# Bridge venv (must use --system-site-packages to see picrawler/vilib)
python3 -m venv --system-site-packages ~/chotu-bridge/.venv
~/chotu-bridge/.venv/bin/pip install fastapi "uvicorn[standard]"
```

Deploy the bridge server from your laptop:

```bash
scp pi_bridge/server.py chotu@chotu.local:~/chotu-bridge/server.py
```

---

## Running

**1. Start llama-server on your laptop:**

```bash
llama-server \
  -m /path/to/Qwen3.5-4B-Q4_K_M.gguf \
  --mmproj /path/to/mmproj-BF16.gguf \
  --port 8080 -ngl 99 -c 16384 --parallel 1
```

**2. Start the Pi bridge (on the Pi):**

```bash
ssh chotu@chotu.local
sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py
```

**3. Start Chotu's brain (on your laptop):**

```bash
source .venv/bin/activate

# Terminal input
python3 -m core.brain

# Voice input (wake word + Whisper)
PALIV_VOICE=1 python3 -m core.brain

# Autonomous goal mode
python3 -m core.brain --goal "find a red object and sit next to it"
```

The browser UI is at **http://localhost:8888** — it shows tool calls, spoken lines, and camera snapshots as they happen.

---

## Talking to Chotu

Just type (or say after the wake word). Some examples:

```
you> walk forward 3 steps then turn around
you> sit down
you> what do you see?
you> do a push-up
you> are you a robot?
you> do some magic
```

In autonomous mode, give it a goal and it pursues it until done:

```bash
python3 -m core.brain --goal "patrol the room and report what you find"
```

---

## Offline testing (no Pi needed)

```bash
python -m scripts.dry_run                    # interactive
python -m scripts.dry_run "walk and say hi"  # one-shot
```

This runs the real brain loop against llama-server but fakes all Pi responses. Useful for tuning personality and checking tool-call behavior when the robot is charging.

---

## Project structure

```
core/
  brain.py          Main agent loop
  llm_client.py     Provider-agnostic LLM wrapper (local llama / Anthropic)
  pi_client.py      Async HTTP client for Pi bridge endpoints
  tools.py          Tool schemas, dispatch map, TTS, vision
  spells.py         Harry Potter spells → Home Assistant
  voice.py          Wake word detection + Whisper STT
  gui_server.py     Browser UI server (FastAPI + SSE)
PALIV.md            Framework contract (states, tool budgets, hard interrupts)
CHOTU.md            Chotu persona (loaded with PALIV.md as system prompt)
habits/             PLAY-state skill prompts (scaffolded for next session)
pi_bridge/
  server.py         Pi-side FastAPI bridge (servos, camera, sensors)
scripts/
  dry_run.py        Offline test harness
```

---

## Configuration flags

| Env var | Default | Effect |
|---|---|---|
| `PALIV_VOICE` | `0` | `1` = wake word + Whisper STT |
| `PALIV_DEBUG` | `0` | `1` = verbose logging |
| `PALIV_MUTE` | `0` | `1` = no audio (logs speech instead) |
| `SPELLS_ENABLED` | all | Comma-separated list, e.g. `avada_kedavra` |
| `HA_BASE_URL` | — | Home Assistant URL for spell integration |
| `HA_TOKEN` | — | Long-lived HA token |
| `HA_LIGHT_ENTITY` | — | Entity ID to control, e.g. `light.living_room` |
