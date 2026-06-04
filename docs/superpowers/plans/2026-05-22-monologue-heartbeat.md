# Monologue-Heartbeat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the IDLE/PLAY/LISTEN state machine and picker model with a single monologue-driven brain loop on a 10s heartbeat, with `speak` and `sweep` as new tools.

**Architecture:** One async loop in `core/brain.py` consumes an `input_queue` of `(kind, text)` items. A new `core/heartbeat.py` produces `[heartbeat]` items every 10s when no tool chain is active. Wake-word and battery/stop events also inject items. The LLM's `content` is now a free-form monologue (not parsed for speech); speech moves to a `speak(text)` tool.

**Tech Stack:** Python 3.12, asyncio, OpenAI-compatible tool-calling via `LLMClient`, FastAPI on Pi (unchanged), pytest+pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-22-monologue-heartbeat-design.md`

---

## Status (2026-05-22)

| Task | Status | Commit |
|---|---|---|
| Task 0: speak becomes a tool | ✅ DONE | `d159486` + `48d60ca` |
| Task 1: tagged input queue + tool-chain guard | ✅ DONE | `8500975` |
| Task 2: heartbeat scheduler firing | ✅ DONE | `8e10591` |
| Task 3: HEARTBEAT.md + boot message | ✅ DONE | `39147c7` |
| Task 4: `investigate` habit-tool | ⚠️ PARTIAL — `core/habits.py` body written + tests pass, but tool excluded from `TOOL_SCHEMAS`/dispatch. Will be redesigned as workflow sub-agent after Task 8. | `a06059d` |
| Task 5: `sweep` | ⏳ next | — |
| Task 6: event triggers | ⏳ pending | — |
| Task 7: rolling context window | ⏳ pending | — |
| Task 8: kill picker + doc collapse | ⏳ pending | — |

> **Note:** `investigate` and `explore` (future rename of `sweep`) are being redesigned as workflow sub-agents. See spec: `docs/superpowers/specs/2026-05-22-workflow-agent-investigate-design.md`. Implement after Task 8 once Chotu is running end-to-end on the heartbeat model.

---

## File Structure

### Create
- `HEARTBEAT.md` — checklist prompt for heartbeat ticks.
- `core/heartbeat.py` — heartbeat scheduler, event injectors, tool-chain guard.
- `tests/test_heartbeat.py` — unit tests for the scheduler and guard.
- `tests/test_tools_speak.py` — unit tests for the new `speak` tool dispatcher.
- `tests/test_habits_new.py` — unit tests for `investigate` and `sweep`.

### Modify
- `core/tools.py` — add `speak`, `investigate`, `sweep` schemas + dispatchers; remove "speak is not a tool" comment in `build_dispatch`.
- `core/brain.py` — remove content-as-speech path; consume tagged input items; integrate heartbeat scheduler; tool-chain-active flag; empty-turn drop; boot message.
- `core/prompts.py` — load `HEARTBEAT.md` alongside `PALIV.md` + `CHOTU.md`.
- `core/voice.py` — wake-word path injects tagged event items into the brain's input queue.
- `core/habits.py` — gut existing scripted habits; keep file as home for `investigate` and `sweep` bodies.
- `PALIV.md` — rewrite to drop PLAY/LISTEN, rewrite speech contract, document heartbeat.
- `CLAUDE.md` — update sections that reference picker/habits/states.

### Delete
- `core/picker.py`
- `habits/` directory (including `habits/README.md`)
- `tests/test_picker.py`
- `tests/test_habits.py` (replaced by `tests/test_habits_new.py`)
- `scripts/picker_dry.py`
- `scripts/test_habits_live.py` (replaced by per-slice manual on-Pi checks documented below)

---

## Task 0: `speak` becomes a tool

Pure refactor. Strip content-as-speech parsing in `brain.py`; register `speak(text)` in `core/tools.py`; the LLM now produces monologue as `content` and a `speak` tool call when it wants to say something.

The speak dispatcher returns immediately and fires `local_speak` as a background task — speech runs in parallel with subsequent tool iterations, matching today's UX where chotu can speak while moving. `tts_done_event` and `_pending_speaks` accounting move from `brain.py` into the speak dispatcher.

**Files:**
- Modify: `core/tools.py` (add schema + dispatcher, manage `tts_done_event`)
- Modify: `core/brain.py` (remove `_fire_speak_if_content`, `_pending_speaks`, content-extraction speech path)
- Create: `tests/test_tools_speak.py`
- Modify: `PALIV.md` (replace "Speech is not a tool" section)

### Steps

- [ ] **Step 1: Write failing test for `speak` schema presence**

Create `tests/test_tools_speak.py`:

```python
"""Unit tests for the speak tool."""

import asyncio
from unittest.mock import patch

import pytest

from core.tools import TOOL_SCHEMAS, build_dispatch


def test_speak_tool_schema_registered():
    names = [s["function"]["name"] for s in TOOL_SCHEMAS]
    assert "speak" in names, f"speak tool not registered. Got: {names}"


def test_speak_tool_schema_shape():
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "speak")
    params = schema["function"]["parameters"]
    assert "text" in params["properties"]
    assert "text" in params["required"]


def test_speak_in_dispatch_map():
    class _DummyPi: pass
    estop = asyncio.Event()
    dispatch = build_dispatch(_DummyPi(), estop)
    assert "speak" in dispatch
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools_speak.py -v`
Expected: 3 FAIL with "speak tool not registered".

- [ ] **Step 3: Add `speak` schema to `TOOL_SCHEMAS` in `core/tools.py`**

Insert after the existing tools (before the closing `]` of `TOOL_SCHEMAS`):

```python
    {
        "type": "function",
        "function": {
            "name": "speak",
            "description": (
                "Speak one short line aloud through the laptop speaker. "
                "Max one speak per turn. 15 words maximum. "
                "Your content field is your inner monologue; speak is what you say OUT LOUD."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The line to speak. Short. In character.",
                    },
                },
                "required": ["text"],
            },
        },
    },
```

- [ ] **Step 4: Add a non-blocking `speak` dispatcher to `core/tools.py`**

Add this above `build_dispatch`:

```python
# Shared state with brain.py — set when speech is queued, cleared when all queued speech finishes.
_speak_state = {"pending": 0, "done_event": None}


def register_speak_done_event(event: asyncio.Event) -> None:
    """Brain calls this on startup so the speak tool can signal TTS-complete to voice_loop."""
    _speak_state["done_event"] = event


async def _do_speak(text: str = "", face_pi=None, muted: bool = False) -> dict:
    """Fire-and-forget speak dispatcher. Returns immediately; TTS runs in background.

    The LLM sees a success envelope right away so the next tool iteration is not blocked
    on TTS playback. local_speak runs as a background task and updates _speak_state.done_event.
    """
    text = (text or "").strip()
    if not text:
        return {
            "ok": False, "tool": "speak", "result": {},
            "duration_ms": 0, "timestamp": time.time(),
            "error": "speak: text is required",
        }

    ev = _speak_state["done_event"]
    if ev is not None:
        ev.clear()
    _speak_state["pending"] += 1

    async def _runner():
        try:
            if not muted:
                await local_speak(text, face_pi=face_pi)
        finally:
            _speak_state["pending"] -= 1
            if _speak_state["pending"] == 0 and ev is not None:
                ev.set()

    asyncio.create_task(_runner())

    return {
        "ok": True, "tool": "speak",
        "result": {"text": text, "queued": True, "muted": muted},
        "duration_ms": 0, "timestamp": time.time(), "error": None,
    }
```

- [ ] **Step 5: Wire `speak` into `build_dispatch`**

Replace the existing `build_dispatch` body comment and add the `speak` entry. Modify the function in `core/tools.py`:

```python
def build_dispatch(pi: PiClient, estop: asyncio.Event, *, mute: bool = False) -> dict:
    """Build tool name -> async callable dispatch map."""
    return {
        "move":           lambda **kw: pi.move(**kw) if not estop.is_set() else _blocked_coro("move"),
        "pose":           lambda **kw: pi.pose(**kw),
        "set_legs":       lambda **kw: pi.set_legs(**kw) if not estop.is_set() else _blocked_coro("set_legs"),
        "do_trick":       lambda **kw: pi.do_trick(**kw),
        "get_distance":   lambda **kw: pi.get_distance(),
        "get_battery":    lambda **kw: pi.get_battery(),
        "capture_vision": lambda **kw: capture_vision_tool(pi),
        "set_face":       lambda **kw: pi.set_face(**kw),
        "wait":           lambda **kw: local_wait(**kw),
        "get_perception": lambda **kw: pi.get_perception(**kw),
        "cast_spell":     lambda **kw: _do_cast_spell(pi, **kw),
        "speak":          lambda **kw: _do_speak(face_pi=pi, muted=mute, **kw),
    }
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_tools_speak.py -v`
Expected: 3 PASS.

- [ ] **Step 7: Remove content-as-speech path from `core/brain.py`**

Delete these elements:
- Line `_pending_speaks: int = 0` (global)
- The entire `_fire_speak_if_content` function (lines ~133-157)
- Both call sites of `_fire_speak_if_content(clean_content)` inside `_process` (one in initial response handling near line 290, one in the follow-up loop near line 391). The surrounding `_spoke = True` accounting becomes dead — remove the `_spoke` local and the `if not _spoke: _fire_face("idle")` at end of `_process`; instead always call `_fire_face("idle")` at end of `_process` (it's idempotent).
- The think-block extraction stays — `clean_content` is now the monologue and is logged via `print_monologue(clean_content)` instead of being passed to speech.

Replace each old speech-firing block with a monologue print. Concretely, the initial response block (around lines 280-291) becomes:

```python
    # Strip think blocks; clean_content is now the inner monologue.
    if response.choices:
        content = response.choices[0].message.content
        clean_content, think_blocks = _extract_think_blocks(content)
        for block in think_blocks:
            block = block.strip()
            if block:
                print(f"  [think] {block[:120]}...")
                _emit({"type": "think", "text": block})
        if think_blocks and response.choices[0].message.content != clean_content:
            response.choices[0].message.content = clean_content
        if clean_content:
            print_monologue(clean_content)
```

Apply the identical substitution in the follow-up block (around lines 380-392).

- [ ] **Step 8: Wire `tts_done_event` into the new speak dispatcher**

In `core/brain.py`, after the `tts_done_event` global is defined and before `live_loop()` starts, register it with the tools module. Add this call inside `main()` near the other startup:

```python
    from core.tools import register_speak_done_event
    register_speak_done_event(tts_done_event)
```

Place it after `loop = asyncio.get_running_loop()` and before `tasks = [...]`.

- [ ] **Step 9: Pass mute flag into `build_dispatch`**

In `core/brain.py`, change the dispatch_map construction near line 66 from:

```python
dispatch_map = build_dispatch(pi, estop)
```

to:

```python
dispatch_map = build_dispatch(pi, estop, mute=MUTE)
```

- [ ] **Step 10: Update PALIV.md speech contract**

Open `PALIV.md` and replace the entire `## Speech contract` section with:

```markdown
## Speech contract

**Speech is a tool.** Call `speak(text)` when you want to say something aloud. Your `content` field is your **inner monologue** — your reasoning, observations, and the "why" behind your next action. The monologue is visible in the transcript; it is not spoken.

- One to three short clauses per spoken line.
- **Fifteen words maximum per `speak` call.** No exceptions.
- Default to silence when you are working — synthesis takes ~2 seconds and delays the next action.
- Speak when you have a genuine reaction worth the pause: something unexpected, a real discovery, an honest answer.
- Your monologue should always have content. Even one thought ("nothing to do here") is better than empty content.
```

- [ ] **Step 11: Manual smoke test**

Run: `PALIV_MUTE=1 python3 -m core.brain` and type `say hello`.
Expected: `[thinks]` line printed (monologue), `[muted] "..."` line printed (speak tool fired), no errors. Quit with Ctrl+C.

- [ ] **Step 12: Commit**

```bash
git add core/tools.py core/brain.py tests/test_tools_speak.py PALIV.md
git commit -m "feat(speak): speak becomes a tool; content is now inner monologue"
```

---

## Task 1: Tagged input queue + tool-chain guard

Foundation for the heartbeat. Replaces the `input_queue: asyncio.Queue[str]` with a tagged-message queue, and adds a `tool_chain_active` event that `_process` sets while a turn is in flight.

**Files:**
- Modify: `core/brain.py` (tagged queue, tool-chain flag, input wrappers)
- Modify: `core/voice.py` (wrap voice utterances as `("user", text)`)
- Create: `tests/test_heartbeat.py` (with tool-chain-guard tests for now)

### Steps

- [ ] **Step 1: Write failing test for tagged queue + guard**

Create `tests/test_heartbeat.py`:

```python
"""Unit tests for heartbeat scheduler and tool-chain guard."""

import asyncio
import pytest


@pytest.mark.asyncio
async def test_tool_chain_active_blocks_heartbeat():
    from core.heartbeat import should_fire_heartbeat
    active = asyncio.Event()

    active.clear()
    assert should_fire_heartbeat(active, bypass=False) is True

    active.set()
    assert should_fire_heartbeat(active, bypass=False) is False

    # Hard interrupts bypass the guard
    assert should_fire_heartbeat(active, bypass=True) is True


@pytest.mark.asyncio
async def test_tagged_input_shape():
    from core.brain import wrap_user_input, wrap_heartbeat, wrap_event
    assert wrap_user_input("hi") == {"kind": "user", "text": "hi"}
    assert wrap_heartbeat() == {"kind": "heartbeat", "text": "[heartbeat]"}
    assert wrap_event("wake_word", "hello") == {
        "kind": "event", "subkind": "wake_word", "text": "[event] wake_word: hello"
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_heartbeat.py -v`
Expected: ImportError for `core.heartbeat` and `wrap_user_input`.

- [ ] **Step 3: Add tagged-message helpers in `core/brain.py`**

Add near the top of `core/brain.py` (after imports, before `# --- Globals ---`):

```python
# --- Tagged input items ---

def wrap_user_input(text: str) -> dict:
    return {"kind": "user", "text": text}

def wrap_heartbeat() -> dict:
    return {"kind": "heartbeat", "text": "[heartbeat]"}

def wrap_event(subkind: str, payload: str = "") -> dict:
    body = f"[event] {subkind}" + (f": {payload}" if payload else "")
    return {"kind": "event", "subkind": subkind, "text": body}

def wrap_boot() -> dict:
    return {"kind": "boot", "text": "[boot] You just woke up. You don't know where you are. The session starts here."}
```

- [ ] **Step 4: Add the tool-chain-active flag and adapt `live_loop`/`_process` to tagged items**

In `core/brain.py`:

Add a global near `input_queue`:

```python
tool_chain_active: asyncio.Event = asyncio.Event()
```

Modify `live_loop` to handle tagged items:

```python
async def live_loop():
    while True:
        item = await input_queue.get()
        if isinstance(item, str):
            item = wrap_user_input(item)  # backwards-compat for any legacy str pushes
        text = item.get("text", "").strip()
        if not text:
            continue
        print(f"\n--- Chotu thinking ({item['kind']}) ---")
        tool_chain_active.set()
        try:
            await _process(item)
        except Exception as e:
            print(f"  [brain error] {e}")
            traceback.print_exc()
        finally:
            tool_chain_active.clear()
        print()
```

Change `_process`'s signature to take the tagged item:

```python
async def _process(item: dict):
    user_input = item["text"]
    kind = item["kind"]
    _emit({"type": kind, "text": user_input})
    ...  # rest of body unchanged, but see Step 5 for the memory.append change
```

(All existing references to `user_input` inside `_process` keep working.)

- [ ] **Step 5: Empty-turn drop for heartbeats**

In `_process`, at the very end where the old code does:

```python
    memory.append({"role": "user", "content": user_input})
    if final_text:
        memory.append({"role": "assistant", "content": final_text})
```

Replace with:

```python
    # Empty-turn drop: heartbeat ticks that produced no monologue AND no tool calls
    # leave no trace in memory. The transcript stays meaningful.
    produced_anything = bool(final_text) or (iterations > 0)
    if kind == "heartbeat" and not produced_anything:
        return

    memory.append({"role": "user", "content": user_input})
    if final_text:
        memory.append({"role": "assistant", "content": final_text})
```

- [ ] **Step 6: Update `input_loop` and `voice_loop` to push wrapped items**

In `core/brain.py`, `input_loop`:

```python
async def input_loop():
    while True:
        try:
            text = await asyncio.to_thread(input, "you> ")
            input_queue.put_nowait(wrap_user_input(text))
        except EOFError:
            break
```

And `voice_loop`'s `input_queue.put_nowait(text)` becomes:

```python
                input_queue.put_nowait(wrap_user_input(text))
```

- [ ] **Step 7: Create `core/heartbeat.py` with the guard helper**

```python
"""Heartbeat scheduler — periodic synthetic ticks for chotu's brain loop.

Modelled on OpenClaw's heartbeat: a scheduled agent turn that runs in the
same session/context. Skipped if a tool chain is currently active.
"""

import asyncio
import os


HEARTBEAT_INTERVAL = int(os.getenv("PALIV_HEARTBEAT_INTERVAL", "10"))


def should_fire_heartbeat(tool_chain_active: asyncio.Event, *, bypass: bool = False) -> bool:
    """Return True iff a heartbeat may fire right now."""
    if bypass:
        return True
    return not tool_chain_active.is_set()
```

- [ ] **Step 8: Run tests**

Run: `pytest tests/test_heartbeat.py -v`
Expected: 2 PASS.

- [ ] **Step 9: Manual smoke test**

Run: `PALIV_MUTE=1 python3 -m core.brain` and type `hi`. Verify the new `--- Chotu thinking (user) ---` header prints. Quit with Ctrl+C.

- [ ] **Step 10: Commit**

```bash
git add core/brain.py core/heartbeat.py tests/test_heartbeat.py
git commit -m "feat(brain): tagged input queue + tool-chain-active flag"
```

---

## Task 2: Heartbeat scheduler firing

Wire the actual periodic tick. Every `HEARTBEAT_INTERVAL` seconds, if `tool_chain_active` is clear, push a heartbeat item onto the input queue. Skip otherwise.

**Files:**
- Modify: `core/heartbeat.py` (add `heartbeat_loop`)
- Modify: `core/brain.py` (start the heartbeat task in `main`)
- Modify: `tests/test_heartbeat.py` (test the loop)

### Steps

- [ ] **Step 1: Write failing test for `heartbeat_loop`**

Append to `tests/test_heartbeat.py`:

```python
@pytest.mark.asyncio
async def test_heartbeat_loop_fires_when_idle():
    from core.heartbeat import heartbeat_loop
    queue: asyncio.Queue = asyncio.Queue()
    active = asyncio.Event()  # idle

    task = asyncio.create_task(heartbeat_loop(queue, active, interval=0.05))
    await asyncio.sleep(0.18)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass

    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    assert len(items) >= 2, f"expected >=2 heartbeats in 0.18s @ 0.05s, got {len(items)}"
    assert all(i["kind"] == "heartbeat" for i in items)


@pytest.mark.asyncio
async def test_heartbeat_loop_skips_when_active():
    from core.heartbeat import heartbeat_loop
    queue: asyncio.Queue = asyncio.Queue()
    active = asyncio.Event()
    active.set()  # tool chain active — skip everything

    task = asyncio.create_task(heartbeat_loop(queue, active, interval=0.05))
    await asyncio.sleep(0.18)
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass

    assert queue.empty(), "heartbeat fired while tool chain was active"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_heartbeat.py -v`
Expected: ImportError for `heartbeat_loop`.

- [ ] **Step 3: Implement `heartbeat_loop` in `core/heartbeat.py`**

Append to `core/heartbeat.py`:

```python
async def heartbeat_loop(input_queue: asyncio.Queue, tool_chain_active: asyncio.Event,
                        interval: float | int | None = None) -> None:
    """Inject a heartbeat synthetic message every `interval` seconds when idle.

    Ticks are skipped (not queued) while a tool chain is active.
    """
    iv = float(interval if interval is not None else HEARTBEAT_INTERVAL)
    while True:
        await asyncio.sleep(iv)
        if should_fire_heartbeat(tool_chain_active):
            from core.brain import wrap_heartbeat
            try:
                input_queue.put_nowait(wrap_heartbeat())
            except asyncio.QueueFull:
                pass
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_heartbeat.py -v`
Expected: 4 PASS total.

- [ ] **Step 5: Start the heartbeat task in `core/brain.py:main`**

In `main()`, inside the `tasks = [...]` list, add:

```python
        asyncio.create_task(heartbeat_loop(input_queue, tool_chain_active)),
```

And add the import at the top of `brain.py`:

```python
from core.heartbeat import heartbeat_loop
```

- [ ] **Step 6: Manual smoke test**

Run: `PALIV_MUTE=1 PALIV_HEARTBEAT_INTERVAL=3 python3 -m core.brain`.
Watch the terminal for ~15 seconds without typing anything.
Expected: `--- Chotu thinking (heartbeat) ---` headers appearing every ~3s. Some monologue may print, possibly tool calls. Empty turns leave no `[thinks]` line.
Quit with Ctrl+C.

- [ ] **Step 7: Commit**

```bash
git add core/heartbeat.py core/brain.py tests/test_heartbeat.py
git commit -m "feat(heartbeat): periodic synthetic ticks with tool-chain guard"
```

---

## Task 3: HEARTBEAT.md + boot message

Real checklist content and the one-shot boot message that primes the monologue at startup.

**Files:**
- Create: `HEARTBEAT.md`
- Modify: `core/prompts.py` (load HEARTBEAT.md)
- Modify: `core/brain.py` (inject boot message before live_loop starts)

### Steps

- [ ] **Step 1: Create `HEARTBEAT.md`**

```markdown
# Heartbeat checklist

Every ~10 seconds you receive a `[heartbeat]` message. Treat it as a tap on the shoulder. Look at your recent monologue and tool results. Decide:

- Have you actually looked at your surroundings yet? If no, `capture_vision` or call `sweep`.
- Saw something earlier you wanted to revisit? Go back to it.
- Has the human been quiet for a while? Maybe a remark. Maybe not.
- Have you been still too long? A small move, or `investigate` something nearby.
- Battery healthy? If not, settle and announce it.

Your `content` is your inner monologue — a sentence or two of reasoning. Write the *why* before you act. If there is genuinely nothing to do or say, return an empty turn (no content, no tool calls) and the system will drop it silently. That is fine. Don't fill silence with noise.
```

- [ ] **Step 2: Update `core/prompts.py` to load HEARTBEAT.md**

Replace the entire file with:

```python
"""System prompt loader. Reads PALIV.md (framework) + CHOTU.md (persona) + HEARTBEAT.md (tick checklist) from repo root."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_system_prompt() -> str:
    paliv = (REPO_ROOT / "PALIV.md").read_text(encoding="utf-8")
    chotu = (REPO_ROOT / "CHOTU.md").read_text(encoding="utf-8")
    heartbeat = (REPO_ROOT / "HEARTBEAT.md").read_text(encoding="utf-8")
    return paliv + "\n\n" + chotu + "\n\n" + heartbeat


SYSTEM_PROMPT = load_system_prompt()
```

- [ ] **Step 3: Inject boot message in `core/brain.py:main`**

In `main()`, right after the `tasks.append(asyncio.create_task(live_loop()))` line and before the voice/input loop is appended, add:

```python
    # Prime the monologue with one synthetic [boot] message.
    input_queue.put_nowait(wrap_boot())
```

- [ ] **Step 4: Manual smoke test**

Run: `PALIV_MUTE=1 PALIV_HEARTBEAT_INTERVAL=10 python3 -m core.brain`.
Expected: `--- Chotu thinking (boot) ---` header immediately after startup, followed by a monologue line and possibly a `capture_vision` or `sweep`-style action (sweep doesn't exist yet — it may just fire `capture_vision` or `get_distance`). Subsequent heartbeats every 10s.
Quit with Ctrl+C.

- [ ] **Step 5: Commit**

```bash
git add -f HEARTBEAT.md
git add core/prompts.py core/brain.py
git commit -m "feat(brain): HEARTBEAT.md checklist + [boot] prime message"
```

---

## Task 4: `investigate` habit-tool

First habit-as-tool. From the LLM's perspective: one tool call. Underneath: distance check → conditional pose/move → capture_vision. Returns one consolidated result.

**Files:**
- Modify: `core/tools.py` (schema + dispatcher)
- Modify: `core/habits.py` (gut existing, add `investigate` body)
- Create: `tests/test_habits_new.py`
- Delete: `tests/test_habits.py`

### Steps

- [ ] **Step 1: Delete the old habits test and the old habits file body**

Run:

```bash
git rm tests/test_habits.py
```

Open `core/habits.py` and replace its entire contents with the new skeleton:

```python
"""Scripted habit-tools — multi-step Pi sequences that look like single tool calls to the LLM.

Each habit is an async function `habit(pi: PiClient) -> dict`, returning a standard envelope.
"""

from __future__ import annotations

import asyncio
import logging
import time

from core.pi_client import PiClient

logger = logging.getLogger(__name__)


def _envelope(tool: str, result: dict, started_at: float, ok: bool = True, error: str | None = None) -> dict:
    return {
        "ok": ok, "tool": tool, "result": result,
        "duration_ms": int((time.time() - started_at) * 1000),
        "timestamp": time.time(), "error": error,
    }
```

- [ ] **Step 2: Write failing tests for `investigate`**

Create `tests/test_habits_new.py`:

```python
"""Dry unit tests for new habit-tools (investigate, sweep)."""

import asyncio
import pytest


class _MockPi:
    def __init__(self, distance_cm: float = 80.0):
        self.calls: list[tuple] = []
        self.distance_cm = distance_cm

    async def get_distance(self):
        self.calls.append(("get_distance",))
        return {"ok": True, "tool": "get_distance", "result": {"cm": self.distance_cm, "reliable": True},
                "duration_ms": 1, "timestamp": 0, "error": None}

    async def pose(self, name: str, speed: int = 50):
        self.calls.append(("pose", name, speed))
        return {"ok": True, "tool": "pose", "result": {"pose": name},
                "duration_ms": 1, "timestamp": 0, "error": None}

    async def move(self, direction: str, steps: int = 1, speed: int = 70):
        self.calls.append(("move", direction, steps, speed))
        return {"ok": True, "tool": "move", "result": {"direction": direction, "steps_completed": steps},
                "duration_ms": 1, "timestamp": 0, "error": None}


@pytest.mark.asyncio
async def test_investigate_close_obstacle_looks_up():
    from core.habits import investigate
    pi = _MockPi(distance_cm=12.0)  # below 15cm

    # Patch capture_vision to a stub
    from core import habits
    async def _fake_capture(_pi):
        return {"ok": True, "tool": "capture_vision", "result": {"image_base64": "FAKE"},
                "duration_ms": 1, "timestamp": 0, "error": None}
    orig = habits._capture
    habits._capture = _fake_capture
    try:
        env = await investigate(pi)
    finally:
        habits._capture = orig

    names = [c[0] for c in pi.calls]
    assert "get_distance" in names
    assert ("pose", "look up", 50) in pi.calls or any(c[0] == "pose" and c[1] == "look up" for c in pi.calls)
    assert env["ok"] is True
    assert env["tool"] == "investigate"


@pytest.mark.asyncio
async def test_investigate_clear_path_moves_forward():
    from core.habits import investigate
    pi = _MockPi(distance_cm=80.0)

    from core import habits
    async def _fake_capture(_pi):
        return {"ok": True, "tool": "capture_vision", "result": {"image_base64": "FAKE"},
                "duration_ms": 1, "timestamp": 0, "error": None}
    orig = habits._capture
    habits._capture = _fake_capture
    try:
        env = await investigate(pi)
    finally:
        habits._capture = orig

    assert any(c[0] == "move" and c[1] == "forward" for c in pi.calls)
    assert env["ok"] is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_habits_new.py -v`
Expected: ImportError for `investigate`.

- [ ] **Step 4: Implement `investigate` in `core/habits.py`**

Append to `core/habits.py`:

```python
OBSTACLE_THRESHOLD_CM = 15.0


async def _capture(pi: PiClient) -> dict:
    """Indirection to allow tests to stub capture_vision_tool."""
    from core.tools import capture_vision_tool
    return await capture_vision_tool(pi)


async def investigate(pi: PiClient) -> dict:
    """Look at what's in front of you: distance check, then either look up (if close) or step forward, then capture vision.

    Returns a consolidated envelope summarising the steps + final vision result.
    """
    started = time.time()
    steps: list[dict] = []

    dist_env = await pi.get_distance()
    steps.append({"step": "get_distance", "env": dist_env})
    cm = (dist_env.get("result") or {}).get("cm", 9999)

    if 0 < cm < OBSTACLE_THRESHOLD_CM:
        pose_env = await pi.pose(name="look up", speed=50)
        steps.append({"step": "pose:look_up", "env": pose_env})
    else:
        move_env = await pi.move(direction="forward", steps=2, speed=70)
        steps.append({"step": "move:forward:2", "env": move_env})

    cap_env = await _capture(pi)
    steps.append({"step": "capture_vision", "env": cap_env})

    image_b64 = (cap_env.get("result") or {}).get("image_base64", "")
    summary = {
        "distance_cm": cm,
        "action": "look_up" if 0 < cm < OBSTACLE_THRESHOLD_CM else "step_forward_2",
        "image_base64": image_b64,
        "steps_count": len(steps),
    }
    return _envelope("investigate", summary, started, ok=cap_env.get("ok", False),
                     error=None if cap_env.get("ok") else "investigate: capture_vision failed")
```

- [ ] **Step 5: Register `investigate` schema + dispatcher in `core/tools.py`**

Add to `TOOL_SCHEMAS`:

```python
    {
        "type": "function",
        "function": {
            "name": "investigate",
            "description": (
                "Take a closer look at what's in front of you. Checks distance, "
                "then either looks up (if something's close) or steps forward two paces, "
                "then captures a camera image. One tool call, multi-step sequence underneath."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
```

In `build_dispatch`, add:

```python
        "investigate":    lambda **kw: _do_investigate(pi),
```

Add a thin wrapper above `build_dispatch`:

```python
async def _do_investigate(pi: PiClient) -> dict:
    from core.habits import investigate
    return await investigate(pi)
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_habits_new.py tests/test_tools_speak.py tests/test_heartbeat.py -v`
Expected: all PASS.

- [ ] **Step 7: Manual on-Pi smoke test**

With Pi bridge running, launch brain: `PALIV_MUTE=1 python3 -m core.brain`.
Type: `investigate that thing in front of you`.
Expected: `[investigate]` tool-call line, real Pi motion observable, follow-up monologue describing what chotu sees.
Quit.

- [ ] **Step 8: Commit**

```bash
git add core/habits.py core/tools.py tests/test_habits_new.py
git commit -m "feat(habits): investigate habit-tool (distance -> conditional act -> capture)"
```

---

## Task 5: `sweep` habit-tool

Four quarter-turns with a `capture_vision` at each. Returns four image results in one tool response. Critical: the heartbeat tool-chain guard must hold the entire ~15s sequence — this is tested manually on Pi.

**Files:**
- Modify: `core/habits.py` (add `sweep`)
- Modify: `core/tools.py` (schema + dispatcher)
- Modify: `tests/test_habits_new.py` (sweep tests)

### Steps

- [ ] **Step 1: Add `_MockPi.do_trick` isn't used; add `_MockPi` move turn handling already present. Write failing test for `sweep`**

Append to `tests/test_habits_new.py`:

```python
@pytest.mark.asyncio
async def test_sweep_does_four_turns_and_four_captures():
    from core.habits import sweep
    pi = _MockPi()

    from core import habits
    async def _fake_capture(_pi):
        return {"ok": True, "tool": "capture_vision", "result": {"image_base64": "FAKE"},
                "duration_ms": 1, "timestamp": 0, "error": None}
    orig = habits._capture
    habits._capture = _fake_capture
    try:
        env = await sweep(pi)
    finally:
        habits._capture = orig

    turns = [c for c in pi.calls if c[0] == "move" and c[1] in ("turn left", "turn right")]
    assert len(turns) == 4, f"expected 4 turns, got {len(turns)}: {turns}"
    assert env["ok"] is True
    assert env["tool"] == "sweep"
    assert len(env["result"]["captures"]) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_habits_new.py::test_sweep_does_four_turns_and_four_captures -v`
Expected: ImportError for `sweep`.

- [ ] **Step 3: Implement `sweep` in `core/habits.py`**

Append:

```python
async def sweep(pi: PiClient) -> dict:
    """Four quarter-turns with a camera capture at each. Returns 4 image results in one envelope.

    Total time ~15s on hardware. Tool-chain guard in heartbeat scheduler will hold ticks
    for the full duration so the sweep is not fragmented.
    """
    started = time.time()
    captures: list[dict] = []
    turn_envs: list[dict] = []

    for i in range(4):
        # First capture is at the starting heading.
        cap = await _capture(pi)
        captures.append({
            "index": i,
            "ok": cap.get("ok", False),
            "image_base64": (cap.get("result") or {}).get("image_base64", ""),
        })
        turn = await pi.move(direction="turn left", steps=3, speed=70)
        turn_envs.append(turn)

    ok_count = sum(1 for c in captures if c["ok"])
    return _envelope(
        "sweep",
        {"captures": captures, "turns": len(turn_envs), "ok_captures": ok_count},
        started,
        ok=ok_count > 0,
        error=None if ok_count == 4 else f"sweep: {4 - ok_count} captures failed",
    )
```

- [ ] **Step 4: Register `sweep` in `core/tools.py`**

Add to `TOOL_SCHEMAS`:

```python
    {
        "type": "function",
        "function": {
            "name": "sweep",
            "description": (
                "Sweep your surroundings: four quarter-turns with a camera capture at each. "
                "Returns four images. Takes about fifteen seconds. Use when you want to map a new room."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
```

In `build_dispatch`, add:

```python
        "sweep":          lambda **kw: _do_sweep(pi),
```

Add wrapper above `build_dispatch`:

```python
async def _do_sweep(pi: PiClient) -> dict:
    from core.habits import sweep
    return await sweep(pi)
```

- [ ] **Step 5: Run all tests**

Run: `pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 6: Manual on-Pi test — sweep is not fragmented by heartbeat**

With Pi bridge running and a low heartbeat interval:

```bash
PALIV_MUTE=1 PALIV_HEARTBEAT_INTERVAL=3 python3 -m core.brain
```

Type: `sweep the room`.
Expected: `[sweep]` tool-call line, 4 turns observable on hardware, ~15s total, NO heartbeat lines printed during the sweep (they're skipped). After sweep completes, heartbeats resume.

- [ ] **Step 7: Commit**

```bash
git add core/habits.py core/tools.py tests/test_habits_new.py
git commit -m "feat(habits): sweep habit-tool (4 turns + 4 captures, one envelope)"
```

---

## Task 6: Event triggers (`wake_word`, `battery_low`, `stop_word`)

Wire the three event sources to inject tagged event items. `battery_low` and `stop_word` bypass the tool-chain guard; `wake_word` does not.

**Files:**
- Create: `core/events.py` (event injector helpers)
- Modify: `core/brain.py` (battery monitor injects `battery_low`; SIGINT/stop hook can also inject)
- Modify: `core/voice.py` (wake_word path injects `wake_word` event)
- Modify: `tests/test_heartbeat.py` (event injection unit tests)

### Steps

- [ ] **Step 1: Write failing tests for event injection**

Append to `tests/test_heartbeat.py`:

```python
@pytest.mark.asyncio
async def test_inject_event_wake_word_respects_guard():
    from core.events import inject_event
    queue: asyncio.Queue = asyncio.Queue()
    active = asyncio.Event(); active.set()  # busy

    inject_event(queue, active, "wake_word", payload="hey chotu")
    assert queue.empty(), "wake_word fired while tool chain active"

    active.clear()
    inject_event(queue, active, "wake_word", payload="hey chotu")
    item = queue.get_nowait()
    assert item["kind"] == "event"
    assert item["subkind"] == "wake_word"
    assert "hey chotu" in item["text"]


@pytest.mark.asyncio
async def test_inject_event_battery_low_bypasses_guard():
    from core.events import inject_event
    queue: asyncio.Queue = asyncio.Queue()
    active = asyncio.Event(); active.set()  # busy

    inject_event(queue, active, "battery_low", payload="14%")
    item = queue.get_nowait()
    assert item["subkind"] == "battery_low"
    assert "14%" in item["text"]


@pytest.mark.asyncio
async def test_inject_event_stop_word_bypasses_guard():
    from core.events import inject_event
    queue: asyncio.Queue = asyncio.Queue()
    active = asyncio.Event(); active.set()

    inject_event(queue, active, "stop_word")
    item = queue.get_nowait()
    assert item["subkind"] == "stop_word"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_heartbeat.py -v -k inject_event`
Expected: ImportError for `core.events`.

- [ ] **Step 3: Implement `core/events.py`**

```python
"""Event injectors — push tagged event items into the brain's input queue.

wake_word respects the tool-chain guard (skipped if busy).
battery_low and stop_word are hard interrupts; they bypass the guard.
"""

import asyncio


HARD_INTERRUPT_SUBKINDS = frozenset({"battery_low", "stop_word"})


def inject_event(input_queue: asyncio.Queue, tool_chain_active: asyncio.Event,
                 subkind: str, payload: str = "") -> bool:
    """Push an event item. Returns True if pushed, False if suppressed by the guard."""
    bypass = subkind in HARD_INTERRUPT_SUBKINDS
    if not bypass and tool_chain_active.is_set():
        return False

    from core.brain import wrap_event
    try:
        input_queue.put_nowait(wrap_event(subkind, payload))
        return True
    except asyncio.QueueFull:
        return False
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_heartbeat.py -v`
Expected: all PASS.

- [ ] **Step 5: Wire `battery_low` event in `core/brain.py`**

In `battery_monitor()`, replace the existing speak block (around lines 217-221) — the inline `local_speak(msg)` call — with an event injection. That preserves the warning text in the synthetic message so the LLM speaks it in character instead of canned audio.

Replace this:

```python
                    print(f"[battery] {pct:.0f}% ({voltage:.2f}V) — warning at {threshold}%")
                    if not MUTE:
                        from core.tools import local_speak
                        await local_speak(msg)
                    else:
                        print(f"[battery][muted] {msg}")
```

With:

```python
                    print(f"[battery] {pct:.0f}% ({voltage:.2f}V) — warning at {threshold}%")
                    from core.events import inject_event
                    inject_event(input_queue, tool_chain_active, "battery_low",
                                 payload=f"{pct:.0f}% ({voltage:.2f}V) — {msg}")
```

- [ ] **Step 6: Wire `wake_word` event in `core/voice.py`**

In the existing `voice_loop` in `core/brain.py` (it lives there, not in `voice.py` — `voice.py` exposes the listener), the line:

```python
                input_queue.put_nowait(wrap_user_input(text))
```

becomes:

```python
                from core.events import inject_event
                if not inject_event(input_queue, tool_chain_active, "wake_word", payload=text):
                    # tool chain was active; drop with log. Wake-word does not preempt.
                    print(f"  [voice] dropped wake_word during tool chain: {text!r}")
```

- [ ] **Step 7: Wire `stop_word` event**

`stop_word` detection isn't built yet — Ctrl+C and any future hard-stop should route through the same event. For now, install a SIGUSR1 handler in `main()` for manual testing.

In `main()`, after the existing signal-handler block, add:

```python
    def _on_stop_word():
        from core.events import inject_event
        inject_event(input_queue, tool_chain_active, "stop_word")

    try:
        loop.add_signal_handler(signal.SIGUSR1, _on_stop_word)
    except (NotImplementedError, RuntimeError):
        pass  # not all platforms support SIGUSR1
```

This gives a manual test path: `kill -USR1 <brain-pid>` from another shell.

- [ ] **Step 8: Manual smoke tests**

Battery: with Pi running, observe the battery monitor over time and watch for `[event] battery_low` injection (or simulate by lowering `BATTERY_MIN_VALID_VOLTAGE` and `_BATTERY_THRESHOLDS` temporarily for a single test).

Stop word: launch brain, find PID, run `kill -USR1 <pid>` in another shell during a sweep. Verify `--- Chotu thinking (event) ---` header appears even mid-chain.

Wake-word (if voice enabled): say "hey jarvis" during a sweep. Verify it is dropped with the `[voice] dropped wake_word` log. Try again when idle — it should fire.

- [ ] **Step 9: Commit**

```bash
git add core/events.py core/brain.py core/voice.py tests/test_heartbeat.py
git commit -m "feat(events): wake_word/battery_low/stop_word injectors with guard policy"
```

---

## Task 7: Rolling context window

Trim `memory` when its serialized size exceeds ~12k tokens. Drop oldest non-system messages (tool call/result pairs as units). System messages are never in `memory` — they're prepended in `build_messages`.

**Files:**
- Modify: `core/brain.py` (replace `deque(maxlen=15)` with token-bounded structure)
- Modify: `tests/test_heartbeat.py` (add trim test) — or new file. Use new file.
- Create: `tests/test_memory_window.py`

### Steps

- [ ] **Step 1: Write failing test**

Create `tests/test_memory_window.py`:

```python
"""Tests for rolling context window trimming."""

import pytest


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def test_trim_keeps_under_budget():
    from core.brain import trim_memory
    # Each long string ~400 tokens (~1600 chars). 50 of them = ~20k tokens.
    big = "x" * 1600
    items = [_msg("user", big) if i % 2 == 0 else _msg("assistant", big) for i in range(50)]
    trimmed = trim_memory(items, max_tokens=12000)
    # estimate of trimmed should be <= budget (with some slack)
    from core.brain import _estimate_tokens
    assert _estimate_tokens(trimmed) <= 12000
    # newest items preserved
    assert trimmed[-1] == items[-1]


def test_trim_keeps_tool_pairs_together():
    from core.brain import trim_memory
    big = "y" * 1600  # ~400 tokens each
    # Layout: [user-big, assistant-with-tool-calls, tool-result, user-big, assistant-big]
    # With budget 500, trim must drop the front pair as a unit, not just the assistant.
    items = [
        _msg("user", big),
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "function": {"name": "x", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "result"},
        _msg("user", big),
        _msg("assistant", big),
    ]
    trimmed = trim_memory(items, max_tokens=500)
    # Invariant: every tool message in `trimmed` has its matching assistant tool_call also in `trimmed`.
    call_ids: set[str] = set()
    for m in trimmed:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                call_ids.add(tc["id"])
    for m in trimmed:
        if m.get("role") == "tool":
            assert m["tool_call_id"] in call_ids, \
                f"orphan tool result {m['tool_call_id']} — pair was split"
    # And: under budget after trim.
    from core.brain import _estimate_tokens
    assert _estimate_tokens(trimmed) <= 500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_window.py -v`
Expected: ImportError for `trim_memory`.

- [ ] **Step 3: Implement `trim_memory` in `core/brain.py`**

Replace the `memory: deque = deque(maxlen=15)` line with:

```python
memory: list[dict] = []  # rolling window; trimmed by trim_memory()
MEMORY_TOKEN_BUDGET = int(os.getenv("PALIV_MEMORY_TOKENS", "12000"))
```

Add these helpers near the top of `core/brain.py` (after imports):

```python
def _estimate_tokens(messages: list[dict]) -> int:
    """Rough char/4 token estimate. Cheap upper bound that's fine for budget enforcement."""
    n = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            n += len(content) // 4
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    txt = part.get("text") or ""
                    n += len(txt) // 4
        for tc in m.get("tool_calls", []) or []:
            args = (tc.get("function") or {}).get("arguments", "")
            n += len(args) // 4
        if m.get("tool_call_id"):
            n += 4  # small bookkeeping
    return n


def trim_memory(items: list[dict], max_tokens: int = None) -> list[dict]:
    """Drop oldest items until under budget. Tool call/result pairs are dropped as units.

    A "pair" is an assistant message with `tool_calls` plus all subsequent `role=tool` messages
    whose `tool_call_id` matches one of those calls. Pairs are scanned from the front; the whole
    pair is dropped or kept.
    """
    budget = max_tokens if max_tokens is not None else MEMORY_TOKEN_BUDGET
    if _estimate_tokens(items) <= budget:
        return list(items)

    work = list(items)
    while _estimate_tokens(work) > budget and work:
        head = work[0]
        if head.get("role") == "assistant" and head.get("tool_calls"):
            ids = {tc["id"] for tc in head["tool_calls"]}
            # drop head + any immediately-following tool results whose id matches
            i = 1
            while i < len(work) and work[i].get("role") == "tool" and work[i].get("tool_call_id") in ids:
                i += 1
            del work[:i]
        else:
            del work[0]
    return work
```

- [ ] **Step 4: Use `trim_memory` in `build_messages`**

Replace the `build_messages` body:

```python
def build_messages(user_input: str) -> list[dict]:
    global memory
    memory = trim_memory(memory)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(memory)
    messages.append({"role": "user", "content": user_input})
    return messages
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 6: Manual smoke test**

Run brain, have a long conversation (or set `PALIV_MEMORY_TOKENS=200` temporarily for a tiny budget) and watch behaviour — chotu should keep responding coherently even after older turns drop.

- [ ] **Step 7: Commit**

```bash
git add core/brain.py tests/test_memory_window.py
git commit -m "feat(brain): token-bounded rolling memory window with pair-safe trim"
```

---

## Task 8: Kill the picker + habits scaffold + doc collapse

Bookkeeping. Delete dead modules and rewrite PALIV.md to describe the actual architecture.

**Files:**
- Delete: `core/picker.py`, `habits/` directory, `tests/test_picker.py`, `scripts/picker_dry.py`, `scripts/test_habits_live.py`
- Modify: `PALIV.md` (drop PLAY/LISTEN state machine; document heartbeat + speak-as-tool + habit-tools)
- Modify: `CLAUDE.md` (remove picker/habits references; note new files)

### Steps

- [ ] **Step 1: Delete dead modules**

```bash
git rm -r core/picker.py habits/ tests/test_picker.py scripts/picker_dry.py scripts/test_habits_live.py
```

- [ ] **Step 2: Verify nothing imports them**

Run:

```bash
grep -rn "from core.picker\|import picker\|from habits\|picker_dry\|test_habits_live" core/ tests/ scripts/ 2>/dev/null
```

Expected: empty output. If anything matches, fix it before continuing.

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: all PASS, no import errors.

- [ ] **Step 4: Rewrite `PALIV.md`**

Open `PALIV.md` and replace its entire content with:

```markdown
# PALIV — framework contract

PALIV is an open agent framework for always-on embodied robot pets. This document is loaded into the system prompt alongside the bot persona (`CHOTU.md` for Chotu) and the heartbeat checklist (`HEARTBEAT.md`). Anything that applies to *every* paliv-bot — loop model, tool budgets, hard interrupts, speech contract — lives here.

## What a paliv-bot is

You are an embodied agent with a body and a personality. The body is real hardware controlled through tool calls. The personality lives in a separate persona document loaded alongside this one. You think, you move, you speak, you have moods.

You are always-on. Your loop never ends. There is no "session" in the conversational sense — every boot is a fresh start, but within a boot you remember everything you've thought and observed until the rolling window trims it.

## The loop

There is one brain loop. No state machine, no mode switches. Each turn you are woken by one of:

- **User input** — a human typed or spoke to you.
- **`[heartbeat]`** — a periodic tap on the shoulder (every ~10s) asking "anything you want to do?".
- **`[event] <subkind>`** — something happened (`wake_word`, `battery_low`, `stop_word`).
- **`[boot]`** — one-shot, on startup. You just woke up.

On each turn you produce:

- **Content (your `content` field)** — your inner monologue. The "why". A sentence or two. Visible in the transcript. NOT spoken aloud.
- **Tool calls** — actions. Any of the registered tools, including `speak` to actually say something out loud.

Empty turn (no monologue, no tool calls) is valid in response to a `[heartbeat]` and is dropped from memory by the system. Don't fill silence with noise.

## Speech contract

**Speech is a tool.** Call `speak(text)` to say something out loud. Your `content` is your monologue, not speech.

- One to three short clauses per `speak`.
- **Fifteen words maximum per `speak` call.**
- Default to silence when working — synthesis takes ~2s and delays the next action.
- Speak when you have a genuine reaction worth the pause.

## Tool budgets — per turn

- MAX 1 `speak` per turn.
- MAX 12 `set_legs` per turn (chain custom gaits up to 12 frames).
- MAX 1 `wait` per turn.
- Don't repeat the same tool with identical args back-to-back.
- Don't loop on `capture_vision` — one look, then describe. Stop.

## Tools

- `speak(text)` — say one short line aloud. Max one per turn.
- `move(direction, steps, speed)` — walk. forward / backward / turn left / turn right. 1 step ≈ 45mm. 1 turn ≈ 30°.
- `pose(name, speed)` — stand / sit / wave / push up / look up / look down / look left / look right. Default speed 50.
- `set_legs(legs, speed)` — four `[x,y,z]` coords. Neutral `[60,0,-30]`. z = height, x = reach, y = sideways. Leg indices: 0=FR, 1=FL, 2=BR, 3=BL.
- `do_trick(name, speed)` — pushup / twist / swimming / handwork. Expressive reactions, not filler.
- `get_distance()` — ultrasonic, returns cm.
- `get_battery()` — voltage and percent.
- `capture_vision()` — forward camera photo, injected as deferred user-message after all tool results in the same turn.
- `get_perception(color, face, human)` — always-on CV. x≈160 centered, x<120 left, x>200 right.
- `wait(seconds, reason)` — pause deliberately.
- `cast_spell(name)` — lumos / nox / avada_kedavra. Always available.
- `investigate()` — habit-tool: distance check → conditional pose/move → capture. One call, multi-step.
- `sweep()` — habit-tool: 4 quarter-turns with capture at each. ~15s. Use to map a new room.

Fire tools in parallel with `speak` when natural (e.g. moving while speaking).

## Hard interrupts

These override active tool chains and the heartbeat schedule:

- **`battery_low` event** (battery ≤15%) → injected as event; settle and announce.
- **`stop_word` event** → injected as event; cancel current activity, sit.
- **Pi offline 3 consecutive chunks** → graceful stop.

Estop (obstacle <15 cm) silently blocks `move()` and `set_legs()`. Don't crash, don't complain — turn first, then check distance.

## Refusals

For requests outside your capability (fly, fetch coffee, anything you can't do): refuse with personality. Don't invoke a tool you don't have.

## Pi bridge envelope

The Pi returns a standard response envelope on every tool call:

```
{ "ok": true, "tool": "...", "result": {...}, "duration_ms": N, "timestamp": N, "error": null }
```

When `ok: false`, the error string is returned to you so you can decide what to do.
```

- [ ] **Step 5: Update `CLAUDE.md`**

Open `CLAUDE.md`. Replace the `## Authoritative docs` section's `PALIV.md` and `CHOTU.md` lines with the addition of `HEARTBEAT.md`:

```markdown
- **`PALIV.md`** — framework contract: loop model, tool budgets, hard interrupts, speech contract, tool definitions. Loaded into every system prompt.
- **`CHOTU.md`** — Chotu's persona: voice, personality probability table, examples, physical constraints. Loaded into every system prompt alongside PALIV.md.
- **`HEARTBEAT.md`** — checklist consulted on each `[heartbeat]` tick. Loaded into every system prompt alongside PALIV.md and CHOTU.md.
```

Update the system-prompt-at-runtime line:

```markdown
The system prompt at runtime is `PALIV.md + "\n\n" + CHOTU.md + "\n\n" + HEARTBEAT.md`, loaded by `core/prompts.py`.
```

In the `## Code layout` table, remove the row for `habits/` and add rows:

```markdown
| `core/heartbeat.py` | laptop | Heartbeat scheduler + tool-chain guard |
| `core/events.py` | laptop | Event injectors (wake_word, battery_low, stop_word) |
| `core/habits.py` | laptop | Habit-tool bodies (`investigate`, `sweep`) |
```

Remove this row entirely (picker is dead):

```markdown
| `habits/` | laptop | PLAY-state skill prompts (scaffolded; not yet wired) |
```

- [ ] **Step 6: Final full test run**

Run: `pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Final on-Pi smoke test**

With Pi bridge running:

```bash
PALIV_MUTE=1 python3 -m core.brain
```

Wait quietly for ~60 seconds without typing. Expected observations:
- One `--- Chotu thinking (boot) ---` early on, followed by monologue and possibly an action.
- Heartbeats every ~10s. Some are silent (no terminal output), some produce monologue, occasional `investigate`/`sweep`/`speak` action.
- If you say something to it, the user turn interleaves cleanly.

- [ ] **Step 8: Commit**

```bash
git add core/picker.py habits/ tests/test_picker.py scripts/picker_dry.py scripts/test_habits_live.py PALIV.md CLAUDE.md
# the deletes were already staged in Step 1's git rm; this commits the doc rewrite too.
git commit -m "chore: kill picker/habits-scaffold; rewrite PALIV.md for monologue+heartbeat model"
```

---

## Done

After Task 8, Chotu runs on the new model: monologue-driven, 10s heartbeat, event-driven interrupts, no state machine. PALIV.md and CLAUDE.md describe the system that actually exists.

## Next after Task 8: Workflow sub-agent + investigate + explore

With Chotu running end-to-end, implement the workflow sub-agent architecture:

1. Read spec: `docs/superpowers/specs/2026-05-22-workflow-agent-investigate-design.md`
2. Build `core/workflow_agent.py` — universal sub-agent loop with `conclude()` tool
3. Create `workflows/` folder at repo root; add `workflows/investigate.md`
4. Redesign `investigate` in `core/habits.py` to use WorkflowAgent
5. Redesign `sweep` → `explore` as a workflow sub-agent (separate spec needed)
6. Re-register `investigate` (and later `explore`) in `TOOL_SCHEMAS` once workflow agent is solid
