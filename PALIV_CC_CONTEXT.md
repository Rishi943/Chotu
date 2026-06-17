# PALIV v1 — Claude Code context

## What this is

paliv-bot is an open agent framework for always-on embodied robot pets.
Chotu (SunFounder PiCrawler quadruped) is the first instance.
Brain runs on Lenovo Legion (CachyOS, Arch-based, Wayland) over LAN to a Pi 5 bridge.

---

## The shift from the old codebase

The old code had `CHOTU_MODE=reactive|goal` and two separate loops:
`brain_loop()` (reactive) and `goal_runner_task()` (autonomous).

**Delete both.** Replace with a single unified live loop and three states.
Delete `CHOTU_MODE` env var entirely.
Delete `system_prompt.py` — content moves to `PALIV.md` + `CHOTU.md`.

---

## Three states

States are code constructs in `brain.py` and `picker.py` — not folders or prompt files.

### IDLE
Ambient mode. Always one habit active, picked by LLM call (thinking-on).
Habits are micro-behaviours: dangle paws, yawn face, shift weight, blink, look around.
Mood variables drive transitions and habit switches.
Chotu can self-initiate into PLAY ("I'm bored, wanna find something?").
Ambient micro-animations (blink, breathe) run always — these are NOT LLM-picked.

### PLAY
Skill execution. Runs in chunks with a tick loop.
Chotu picks a habit (e.g. explore) based on mood + context (LLM call).
Runs until habit completes, hard interrupt fires, or Chotu gives up.
cast_spell, do_trick, and HA tools are NOT available in PLAY.

### LISTEN
Interrupt mechanic — not a permanent state.
Wakeword fires a flag. Loop waits for current chunk to finish
(cannot cut mid-tool-call — hardware may be mid-step. Max lag = one chunk ~5–10s).
LLM responds in character. Then returns to prior state (IDLE or PLAY).

State flow diagram is in `paliv-v1-flow.html` — refer to it for transition logic.

---

## Picker (`picker.py` — new file)

One LLM call (thinking-on, small model) that returns one of:
- A habit name (when in IDLE or PLAY, choosing next behaviour)
- A state transition (IDLE → PLAY, with habit name)

Input: mood state as natural language (never raw numbers).
Output: single token — habit name or transition directive.

Mood variables: curiosity, boredom, attention, battery.
Translated before picker call, e.g.:
- boredom=0.8 → "very bored, nothing has happened in a while"
- battery=0.12 → "battery critical, must rest"

---

## Hard interrupts — override everything

- Battery ≤15% → force IDLE, speak once, stay
- Stop word → cancel habit, sit, wait
- Pi offline 3 consecutive chunks → graceful stop

---

## Key rules — enforce in code

- One state active at a time
- MAX 1 speak() per turn (speak is message content, not a tool)
- MAX 12 set_legs() per turn
- MAX 1 wait() per turn
- cast_spell / do_trick / HA tools: IDLE and LISTEN only
- goal_complete tool: PLAY only
- Battery ≤15% forces IDLE — checked before every picker call
- Estop blocks move() and set_legs() silently — never crash

---

## Hardware split

**Pi 5** (`chotu.local:7000`): FastAPI bridge only.
Endpoints: /health /move /pose /set_legs /trick /speak /distance /capture /battery /perception /face
Zero LLM on Pi. Piper TTS runs on Pi for voice output.

**Legion** (runs brain): all inference via llama-server (port 8080, OpenAI-compatible API).
Default model: `Qwen3.5-4B-Q4_K_M.gguf`
Cloud fallback: `claude-sonnet-4-6` via `CHOTU_LLM_PROVIDER=claude`

---

## File structure

```
paliv/
├── CLAUDE.md                  ← you are reading this
├── PALIV.md                   ← framework definition
├── CHOTU.md                   ← Chotu personality
├── habits/                    ← prompt files for things Chotu does within states
│   └── explore/
│       └── HABIT.md
└── paliv-bot/                 ← python package
    ├── brain.py               ← unified live loop, states live here as code
    ├── picker.py              ← NEW: state + habit picker
    ├── llm_client.py
    ├── pi_client.py
    ├── tools.py
    ├── voice.py
    ├── gui_server.py
    ├── spells.py
    └── server.py              ← Pi only, do not run on laptop
```

---

## Build order for this session

1. **Refactor first** — rename package to paliv-bot, delete CHOTU_MODE, split system_prompt.py into PALIV.md + CHOTU.md
2. **`picker.py`** — LLM call, mood → habit name or state transition
3. **`habits/explore/HABIT.md`** — explore habit prompt
4. **`brain.py`** — unified live loop (IDLE state first, PLAY + LISTEN as stubs)
5. Wire ambient micro-animations (weighted random, no LLM)

Do not start PLAY or LISTEN until IDLE picker is verified working.

---

## Chotu's personality (short version)
we will develop this more but for now:

Full personality should always be in `CHOTU.md`.
Key rules for any spoken output:
- 15 words max per spoken line
- Sardonic, dry, occasionally genuinely delighted
- 40% dark side, 45% humour, 30% curiosity breaks character (gear shift, fully drops act)
