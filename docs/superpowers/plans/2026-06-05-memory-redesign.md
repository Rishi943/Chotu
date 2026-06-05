# Memory Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Chotu short-term visual memory (last 4 frames as images, older stripped to text) and persist full turn transcripts (tool calls + results) to memory, replacing the current text-only persistence — while keeping context small and prompt-cache-friendly.

**Architecture:** `core/brain.py` keeps a module-level `memory: list[dict]`. Today only two text lines per turn are saved (`brain.py:559-560`). We change `_process` to persist the whole turn slice, add `enforce_frame_window()` to bound image-frames to the newest N, and keep the existing `trim_memory()` as the text-token ceiling. Persistence logic is extracted into a pure `persist_turn()` helper so it is unit-testable without the async loop.

**Tech Stack:** Python 3.12, pytest, `core/brain.py`, `core/llm_client.py` (provides `format_assistant_message`).

---

## File Structure

- `core/brain.py` — add `PALIV_FRAME_WINDOW` config, `_is_frame_msg()`, `enforce_frame_window()`, `persist_turn()`; rewire end of `_process`. All memory-shaping lives here next to `trim_memory`/`evict_old_heartbeats`.
- `tests/test_frame_window.py` — new; unit tests for `_is_frame_msg` + `enforce_frame_window`.
- `tests/test_persist_turn.py` — new; unit tests for `persist_turn` (incl. frame window + empty-heartbeat skip).
- `tests/test_memory_window.py` — extend with a no-orphan invariant test for `trim_memory` over full persisted turns.
- `scripts/measure_fps_budget.py` — rewrite the simulated turn to match real persistence + the 4-frame window.

---

## Task 1: Frame window — config, `_is_frame_msg`, `enforce_frame_window`

**Files:**
- Modify: `core/brain.py` (add config near line 36; add functions after `trim_memory`, ~line 131)
- Test: `tests/test_frame_window.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_frame_window.py`:

```python
from core.brain import _is_frame_msg, enforce_frame_window


def _frame():
    return {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
        {"type": "text", "text": "view"},
    ]}


def _text(role, txt):
    return {"role": role, "content": txt}


def _mem(n_frames):
    mem = []
    for i in range(n_frames):
        mem.append(_frame())
        mem.append(_text("assistant", f"desc{i}"))
    return mem


def test_is_frame_msg_true_for_image_user():
    assert _is_frame_msg(_frame()) is True


def test_is_frame_msg_false_for_text():
    assert _is_frame_msg(_text("user", "hi")) is False
    assert _is_frame_msg(_text("assistant", "yo")) is False


def test_keeps_only_last_4_frames():
    mem = _mem(6)
    enforce_frame_window(mem, keep=4)
    frames = [m for m in mem if _is_frame_msg(m)]
    stubs = [m for m in mem if m.get("_origin") == "frame_stripped"]
    assert len(frames) == 4
    assert len(stubs) == 2


def test_noop_when_at_or_under_keep():
    mem = _mem(4)
    before = [dict(m) for m in mem]
    enforce_frame_window(mem, keep=4)
    assert mem == before


def test_idempotent():
    mem = _mem(6)
    enforce_frame_window(mem, keep=4)
    once = [dict(m) for m in mem]
    enforce_frame_window(mem, keep=4)
    assert mem == once


def test_keep_zero_strips_all():
    mem = _mem(3)
    enforce_frame_window(mem, keep=0)
    assert [m for m in mem if _is_frame_msg(m)] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_frame_window.py -v`
Expected: FAIL — `ImportError: cannot import name '_is_frame_msg' from 'core.brain'`

- [ ] **Step 3: Add the config constant**

In `core/brain.py`, find the config block (the line `HEARTBEAT_WINDOW = int(os.getenv("PALIV_HEARTBEAT_WINDOW", "5"))` is nearby, ~line 36). Add directly below it:

```python
PALIV_FRAME_WINDOW = int(os.getenv("PALIV_FRAME_WINDOW", "4"))
```

- [ ] **Step 4: Implement the functions**

In `core/brain.py`, immediately after `trim_memory` (after its `return work`, ~line 131) and before `def evict_old_heartbeats`, add:

```python
def _is_frame_msg(m: dict) -> bool:
    """True for a persisted camera frame: a user message whose content list
    contains an image_url part."""
    c = m.get("content")
    return (
        m.get("role") == "user"
        and isinstance(c, list)
        and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c)
    )


def enforce_frame_window(memory: list[dict], keep: int = None) -> None:
    """Keep image bytes only for the newest `keep` frames; replace older frames
    with a tiny text stub (their meaning survives in the following assistant
    description). Mutates `memory` in place. Idempotent. No-op when frames <= keep."""
    keep = PALIV_FRAME_WINDOW if keep is None else keep
    frame_idxs = [i for i, m in enumerate(memory) if _is_frame_msg(m)]
    to_strip = frame_idxs if keep <= 0 else frame_idxs[:-keep]
    for i in to_strip:
        memory[i] = {
            "role": "user",
            "content": "[earlier camera frame — see description below]",
            "_origin": "frame_stripped",
        }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_frame_window.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add core/brain.py tests/test_frame_window.py
git commit -m "feat(memory): bounded frame window (enforce_frame_window)"
```

---

## Task 2: `persist_turn` helper

**Files:**
- Modify: `core/brain.py` (add `persist_turn` after `enforce_frame_window`)
- Test: `tests/test_persist_turn.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_persist_turn.py`:

```python
from core.brain import persist_turn, _is_frame_msg


def _frame():
    return {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
        {"type": "text", "text": "view"},
    ]}


def test_empty_heartbeat_persists_nothing():
    mem = []
    persist_turn(mem, [{"role": "user", "content": "[heartbeat]"}], kind="heartbeat", iterations=0)
    assert mem == []


def test_normal_turn_appended():
    mem = []
    turn = [
        {"role": "user", "content": "walk"},
        {"role": "assistant", "content": "ok", "tool_calls": [{"id": "c1", "type": "function",
            "function": {"name": "move", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        {"role": "assistant", "content": "done"},
    ]
    persist_turn(mem, turn, kind="user", iterations=1)
    assert mem == turn


def test_frame_window_enforced_after_extend():
    # 5 frames already in memory, one more arrives this turn -> only 4 images kept
    mem = []
    for _ in range(5):
        mem.append(_frame())
        mem.append({"role": "assistant", "content": "d"})
    new_turn = [{"role": "user", "content": "[heartbeat]"}, _frame(),
                {"role": "assistant", "content": "d6"}]
    persist_turn(mem, new_turn, kind="heartbeat", iterations=1)
    assert len([m for m in mem if _is_frame_msg(m)]) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_persist_turn.py -v`
Expected: FAIL — `ImportError: cannot import name 'persist_turn'`

- [ ] **Step 3: Implement `persist_turn`**

In `core/brain.py`, immediately after `enforce_frame_window`, add:

```python
def persist_turn(memory: list[dict], turn_msgs: list[dict], kind: str, iterations: int) -> None:
    """Append a completed turn's messages to memory, then bound the frame window.
    Empty heartbeat turns (no tool activity) persist nothing. Mutates `memory`."""
    if kind == "heartbeat" and iterations == 0:
        return
    memory.extend(turn_msgs)
    enforce_frame_window(memory)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_persist_turn.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add core/brain.py tests/test_persist_turn.py
git commit -m "feat(memory): persist_turn helper (full-turn persistence + frame window)"
```

---

## Task 3: Wire `persist_turn` into `_process`

**Files:**
- Modify: `core/brain.py` — `_process` (capture `prefix_len` after `build_messages`/evict; replace the two-line append at the end, ~lines 553-560)

- [ ] **Step 1: Capture `prefix_len` after the heartbeat-evict block**

In `core/brain.py` `_process`, find:

```python
    messages = build_messages(user_input, origin=kind)
    if kind == "heartbeat":
        evict_old_heartbeats(messages)
    dbg(f"sending {len(messages)} messages to LLM")
```

Replace with (adds one line — the current user message is always the last element, so this is robust to `evict_old_heartbeats` having changed the length):

```python
    messages = build_messages(user_input, origin=kind)
    if kind == "heartbeat":
        evict_old_heartbeats(messages)
    prefix_len = len(messages) - 1  # index of the current user msg; turn content starts here
    dbg(f"sending {len(messages)} messages to LLM")
```

- [ ] **Step 2: Replace the end-of-`_process` persistence**

In `core/brain.py` `_process`, find the current ending:

```python
    final_text = response.choices[0].message.content
    _fire_face("idle")

    if kind == "heartbeat" and iterations == 0:
        return

    memory.append({"role": "user", "content": user_input, "_origin": kind})
    memory.append({"role": "assistant", "content": final_text or "", "_origin": kind})
```

Replace with:

```python
    final_text = response.choices[0].message.content
    _fire_face("idle")

    # The final (no-tool) assistant reply is not appended inside the loop, so add it
    # now; guarantee a content field for resend validity.
    final_msg = llm_client.format_assistant_message(response)
    final_msg.setdefault("content", final_text or "")
    messages.append(final_msg)

    # Persist the whole turn (user + assistant/tool_calls + tool results + frames +
    # replies). Empty heartbeats persist nothing; frame window bounds images to 4.
    persist_turn(memory, messages[prefix_len:], kind, iterations)
```

- [ ] **Step 3: Run the full suite to confirm no regression**

Run: `python -m pytest -q`
Expected: PASS (all existing tests + the new ones from Tasks 1-2)

- [ ] **Step 4: Manual smoke test against the local model**

Pre-req: llama-server running. Run:

`python -m scripts.dry_run "walk forward"`

Expected: completes a turn (move + speak) without error. (The dry-run fakes the Pi; the point is that the new persistence path does not raise.)

- [ ] **Step 5: Commit**

```bash
git add core/brain.py
git commit -m "feat(memory): persist full turns + bound frames in _process"
```

---

## Task 4: `trim_memory` no-orphan invariant over full turns

**Files:**
- Test: `tests/test_memory_window.py` (extend)

- [ ] **Step 1: Write the failing/regression test**

Append to `tests/test_memory_window.py`:

```python
from core.brain import trim_memory, _is_frame_msg


def _full_turn(i):
    return [
        {"role": "user", "content": f"[heartbeat] {i}", "_origin": "heartbeat"},
        {"role": "assistant", "content": "look",
         "tool_calls": [{"id": f"c{i}", "type": "function",
                         "function": {"name": "capture_vision", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": "Camera snapshot taken."},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            {"type": "text", "text": "view"}]},
        {"role": "assistant", "content": f"saw thing {i}"},
    ]


def test_trim_never_orphans_tool_results():
    mem = []
    for i in range(40):
        mem.extend(_full_turn(i))
    trimmed = trim_memory(mem, max_tokens=200)  # force aggressive trimming
    # every role=tool must be preceded somewhere earlier by an assistant with a
    # matching tool_call id (no orphaned tool results after trimming from the front)
    seen_ids = set()
    for m in trimmed:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls", []) or []:
                seen_ids.add(tc["id"])
        if m.get("role") == "tool":
            assert m["tool_call_id"] in seen_ids, "orphaned tool result after trim"
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_memory_window.py::test_trim_never_orphans_tool_results -v`
Expected: PASS (existing `trim_memory` already drops assistant+tool as a unit from the front). If it FAILS, that is a real `trim_memory` bug to fix before proceeding — but no code change is expected.

- [ ] **Step 3: Commit**

```bash
git add tests/test_memory_window.py
git commit -m "test(memory): trim_memory never orphans tool results over full turns"
```

---

## Task 5: Fix `measure_fps_budget.py` to model real persistence

**Files:**
- Modify: `scripts/measure_fps_budget.py`

- [ ] **Step 1: Replace the simulated turn block + loop**

The current script invents a per-turn block that re-injects frames + tool results every turn (which the brain does NOT do pre-redesign, and post-redesign frames are bounded to 4). Replace the `_turn_block` function and the loop in `main` so each persisted turn keeps text but the frame window caps images at `PALIV_FRAME_WINDOW`.

In `scripts/measure_fps_budget.py`, change the docstring's loop note and replace `_turn_block` with:

```python
def _turn_block(b64: str, with_frame: bool) -> list[dict]:
    """One persisted turn. `with_frame` carries the image; stripped (older) turns
    keep only the text stub, mirroring enforce_frame_window."""
    frame_part = _frame_msg(b64) if with_frame else {
        "role": "user", "content": "[earlier camera frame — see description below]"}
    return [
        {"role": "user", "content": "[heartbeat]"},
        {"role": "assistant", "content": "*Scanning the room.*",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "capture_vision", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "Camera snapshot taken."},
        frame_part,
        {"role": "assistant", "content": "A desk, a monitor, my charging cable on the floor.",
         "tool_calls": [{"id": "c2", "type": "function",
                         "function": {"name": "speak",
                                      "arguments": json.dumps({"text": "I see my charging cable."})}}]},
        {"role": "tool", "tool_call_id": "c2",
         "content": json.dumps({"ok": True, "tool": "speak", "result": {"played": True},
                                "duration_ms": 1200, "timestamp": 0, "error": None})},
    ]
```

Replace the simulated-loop section in `main` (the `memory: list[dict] = []` block) with one that strips frames beyond the window:

```python
    from core.brain import PALIV_FRAME_WINDOW
    print(f"1-FPS LOOP — full context per turn (frame window = {PALIV_FRAME_WINDOW}):\n")
    print(f"  {'turn':<6}{'full input':>12}{'delta':>10}{'images kept':>14}")
    memory: list[dict] = []
    prev = 0
    blocks: list[list[dict]] = []
    for t in range(1, args.turns + 1):
        msgs = [sys_msg] + memory + [{"role": "user", "content": "[heartbeat]"}]
        full = await _tok(llm, msgs, TOOL_SCHEMAS)
        imgs = min(t - 1, PALIV_FRAME_WINDOW)
        print(f"  {t:<6}{full:>12}{full - prev:>10}{imgs:>14}")
        prev = full
        # append this turn; then strip frames older than the window
        blocks.append(_turn_block(b64, with_frame=True))
        keep_from = max(0, len(blocks) - PALIV_FRAME_WINDOW)
        memory = []
        for j, blk in enumerate(blocks):
            memory += _turn_block(b64, with_frame=(j >= keep_from))
```

- [ ] **Step 2: Run it (Pi on, llama-server up)**

Run: `python -m scripts.measure_fps_budget --turns 8`
Expected: "images kept" column rises 0,1,2,3,4,4,4,4 (caps at the window); per-turn delta stabilizes once frames stop accumulating.

- [ ] **Step 3: Commit**

```bash
git add scripts/measure_fps_budget.py
git commit -m "fix(scripts): measure_fps_budget models real persistence + frame window"
```

---

## Self-Review notes (already applied)

- **Spec coverage:** full-turn persistence (Task 3), `enforce_frame_window`/`PALIV_FRAME_WINDOW` (Task 1), `persist_turn` (Task 2), text ceiling no-orphan invariant (Task 4), measurement-script fix (Task 5). All spec sections mapped.
- **Final-reply gap:** the spec's persistence snippet assumed the final assistant reply was in `messages`; it is not (loop only appends tool-call assistant messages). Task 3 Step 2 appends it explicitly with a guaranteed `content` field.
- **Types/names consistent:** `_is_frame_msg`, `enforce_frame_window(memory, keep)`, `persist_turn(memory, turn_msgs, kind, iterations)`, `PALIV_FRAME_WINDOW` used identically across tasks.
- **No compaction:** deferred per spec non-goals; not in any task.
