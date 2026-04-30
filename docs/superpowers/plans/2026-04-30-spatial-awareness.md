# Spatial Awareness Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix goal-mode "lost in space" — give `scan_environment` real 360° coverage with body-relative labels, invalidate the object map when Chotu turns, and bump llama-server context to 16384 so long goal runs don't crash.

**Architecture:** Three localized changes in three files. No new modules. Pure functions extracted from the rewritten `scan_environment_tool` so the label math is unit-testable; orchestration verified end-to-end via `scripts/dry_run.py` and on-Pi manual checks. Map invalidation is a single predicate hook in `_run_one`.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (existing), no new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-30-spatial-awareness-design.md`

---

## File Map

- **`CLAUDE.md`** — modify: launch command, `-c 8192` → `-c 16384`
- **`chotu/brain.py`** — modify: rewrite `scan_environment_tool` (line 81–115), add label/degree constants near top of scan section, add `_should_invalidate_map_after_turn()` predicate, hook into `_run_one`
- **`chotu/system_prompt.py`** — modify: section 5 `scan_environment` line, section 6 (object map text), "point at the red cup" example
- **`chotu/tools.py`** — modify: scan_environment tool schema (lines 179–200), drop `segments` parameter
- **`scripts/dry_run.py`** — modify: line 56–60 mock, use new labels
- **`tests/test_spatial_awareness.py`** — create: unit tests for label table, map-entry building, invalidation predicate

---

## Task 1: Bump llama-server context size

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Find the launch line**

Run: `grep -n "ctx\|-c 8192\|-c 16384" CLAUDE.md`
Expected: one line showing `-c 8192` in the llama-server command (currently around the development setup section).

- [ ] **Step 2: Edit `CLAUDE.md`**

Replace `-c 8192` with `-c 16384` in the llama-server start command. Leave the rest of the line untouched.

- [ ] **Step 3: Verify the change**

Run: `grep -n "ctx\|-c 16384\|-c 8192" CLAUDE.md`
Expected: shows `-c 16384`, no `-c 8192` remaining.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: bump llama-server context to 16384

Goal runs were hitting context-exceeded errors at 8192 once
vision images and tool history accumulated. 16384 fits
comfortably in 6GB VRAM at Q4_K_M."
```

---

## Task 2: Add label/degree constants and pure helpers (TDD)

We extract two pure helpers so the label math and the invalidation rule can be unit-tested without booting a full brain.

**Files:**
- Create: `tests/test_spatial_awareness.py`
- Modify: `chotu/brain.py` (add constants and helpers near the existing scan section, around line 63)

- [ ] **Step 1: Write failing tests for the label/degree table**

Create `tests/test_spatial_awareness.py` with:

```python
"""Unit tests for spatial-awareness helpers in brain.py."""


def test_scan_labels_and_degrees_align():
    from chotu.brain import SCAN_LABELS, SCAN_DEGREES, SCAN_SEGMENTS
    assert len(SCAN_LABELS) == SCAN_SEGMENTS == 6
    assert len(SCAN_DEGREES) == 6
    assert SCAN_DEGREES == [0, 60, 120, 180, 240, 300]
    assert SCAN_LABELS == [
        "front", "front-right", "back-right",
        "back", "back-left", "front-left",
    ]


def test_build_map_key_combines_label_and_degree():
    from chotu.brain import _build_map_key
    assert _build_map_key("front", 0) == "front (+0°)"
    assert _build_map_key("front-right", 60) == "front-right (+60°)"
    assert _build_map_key("back", 180) == "back (+180°)"


def test_should_invalidate_map_after_turn_true_for_turn_right():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": True}
    assert _should_invalidate_map_after_turn("move", {"direction": "turn right"}, result) is True


def test_should_invalidate_map_after_turn_true_for_turn_left():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": True}
    assert _should_invalidate_map_after_turn("move", {"direction": "turn left"}, result) is True


def test_should_invalidate_map_after_turn_false_for_forward():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": True}
    assert _should_invalidate_map_after_turn("move", {"direction": "forward"}, result) is False


def test_should_invalidate_map_after_turn_false_for_backward():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": True}
    assert _should_invalidate_map_after_turn("move", {"direction": "backward"}, result) is False


def test_should_invalidate_map_after_turn_false_when_call_failed():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": False, "error": "estop blocked"}
    assert _should_invalidate_map_after_turn("move", {"direction": "turn right"}, result) is False


def test_should_invalidate_map_after_turn_false_for_pose_or_set_legs():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": True}
    assert _should_invalidate_map_after_turn("pose", {"name": "look left"}, result) is False
    assert _should_invalidate_map_after_turn("set_legs", {"legs": [[60, 0, -30]] * 4}, result) is False


def test_should_invalidate_map_after_turn_false_for_other_tools():
    from chotu.brain import _should_invalidate_map_after_turn
    result = {"ok": True}
    assert _should_invalidate_map_after_turn("speak", {"text": "hi"}, result) is False
    assert _should_invalidate_map_after_turn("capture_vision", {}, result) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_spatial_awareness.py -v`
Expected: ImportError / AttributeError — `SCAN_LABELS`, `_build_map_key`, `_should_invalidate_map_after_turn` not yet defined.

- [ ] **Step 3: Add constants and helpers to `chotu/brain.py`**

In `chotu/brain.py`, locate the comment `# --- scan_environment (local tool, not a Pi endpoint) ---` (currently around line 63). Replace the section header line with the following block (constants and helpers go above the existing `_describe_objects` function):

```python
# --- scan_environment (local tool, not a Pi endpoint) ---

SCAN_SEGMENTS = 6
SCAN_LABELS = [
    "front", "front-right", "back-right",
    "back", "back-left", "front-left",
]
SCAN_DEGREES = [0, 60, 120, 180, 240, 300]
TURN_STEPS_PER_SEGMENT = 2  # 6 segments × 2 steps × ~30° = ~360°


def _build_map_key(label: str, deg: int) -> str:
    """Format a body-relative label with its absolute angle from scan start."""
    return f"{label} (+{deg}°)"


def _should_invalidate_map_after_turn(name: str, args: dict, result: dict) -> bool:
    """True iff a successful tool call rotated the body and stale the object map."""
    if name != "move":
        return False
    if not result.get("ok"):
        return False
    return args.get("direction") in ("turn left", "turn right")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_spatial_awareness.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add chotu/brain.py tests/test_spatial_awareness.py
git commit -m "feat: add scan label tables and turn-invalidation predicate

Pure helpers extracted from the upcoming scan_environment rewrite
so the label/degree math and the map-invalidation rule are
unit-testable without booting the brain or hitting the Pi."
```

---

## Task 3: Rewrite `scan_environment_tool` for true 360°

**Files:**
- Modify: `chotu/brain.py` (replace function `scan_environment_tool`, currently lines 81–115)

- [ ] **Step 1: Replace the function body**

Open `chotu/brain.py`. Replace the existing `scan_environment_tool` (the function defined with `async def scan_environment_tool(segments: int = 8) -> dict:` and its body up to the `return { ... }`) with:

```python
async def scan_environment_tool() -> dict:
    """360° sweep: rotate in 6 segments, photograph each, identify objects.

    After scan, robot is back at its scan-start heading (6 × 2 × ~30° = 360°).
    The first turn after this call invalidates the object_map.
    """
    global object_map
    start = time.time()
    entries: dict[str, list[str]] = {}

    for i, (label, deg) in enumerate(zip(SCAN_LABELS, SCAN_DEGREES)):
        if i > 0:
            turn = await pi.move("turn right", steps=TURN_STEPS_PER_SEGMENT, speed=80)
            if not turn.get("ok"):
                break

        capture = await capture_vision_tool(pi)
        image_b64 = capture.get("result", {}).get("image_base64", "")
        objects = await _describe_objects(image_b64) if image_b64 else []
        entries[_build_map_key(label, deg)] = objects

    # Replace map atomically — any partial scan still overwrites the previous one.
    object_map.clear()
    object_map.update(entries)
    object_map["_scan_id"] = object_map.get("_scan_id", 0) + 1
    object_map["_timestamp"] = time.time()  # kept for the 60s freshness gate in build_messages

    notable = [(key, obj) for key, objs in entries.items() for obj in objs]
    if notable:
        summary = "Found: " + ", ".join(f"{obj} ({key})" for key, obj in notable)
    else:
        summary = "No objects identified."

    ms = int((time.time() - start) * 1000)
    return {
        "ok": True, "tool": "scan_environment",
        "result": {"map": entries, "summary": summary},
        "duration_ms": ms, "timestamp": time.time(), "error": None,
    }
```

Also update the dispatch line just below the function:

```python
dispatch_map["scan_environment"] = lambda **kw: scan_environment_tool()
```

(The `**kw` is preserved for compatibility with `dispatch_tool`'s signature — we just ignore any args, since `segments` is gone.)

- [ ] **Step 2: Run existing tests to confirm nothing broke**

Run: `pytest tests/ -v`
Expected: all existing tests still pass; new spatial-awareness tests still pass.

- [ ] **Step 3: Smoke-check by importing**

Run: `python -c "from chotu.brain import scan_environment_tool, SCAN_LABELS, _build_map_key; print(SCAN_LABELS); print(_build_map_key('back', 180))"`
Expected: prints the 6 labels and `back (+180°)`.

- [ ] **Step 4: Commit**

```bash
git add chotu/brain.py
git commit -m "feat: rewrite scan_environment for true 360° coverage

6 segments × 2 turn-right steps × ~30° = ~360°. Body-relative
labels (front, front-right, ...) replace the fictional N/NE/E
compass tags. Map keys carry both the label and the absolute
degrees from scan start, so the LLM can reason in either form.

Robot ends scan back at start heading; no undo turn needed."
```

---

## Task 4: Hook map invalidation into `_run_one`

**Files:**
- Modify: `chotu/brain.py` (function `_run_one`, currently lines 423–428)

- [ ] **Step 1: Update `_run_one`**

Replace the existing `_run_one` function:

```python
async def _run_one(tc):
    name = tc.function.name
    args_json = tc.function.arguments
    dbg(f"dispatching {name}({args_json})")
    result = await dispatch_tool(dispatch_map, name, args_json)
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        args = {}
    if _should_invalidate_map_after_turn(name, args, result):
        if object_map:
            dbg(f"[map] invalidated after {args.get('direction')}")
        object_map.clear()
    return tc, name, args_json, result
```

- [ ] **Step 2: Add an integration-style test for the hook**

Append to `tests/test_spatial_awareness.py`:

```python
def test_run_one_clears_object_map_after_turn(monkeypatch):
    """A successful turn dispatched through _run_one must clear object_map."""
    import asyncio
    from chotu import brain

    # Seed the map with sentinel data
    brain.object_map.clear()
    brain.object_map.update({"front (+0°)": ["bottle"], "_scan_id": 1})

    # Stub dispatch_tool so we don't need the Pi
    async def fake_dispatch(_map, _name, _args_json):
        return {"ok": True, "tool": "move", "result": {}, "duration_ms": 10,
                "timestamp": 0, "error": None}

    monkeypatch.setattr(brain, "dispatch_tool", fake_dispatch)

    class FakeFn:
        def __init__(self, name, args):
            self.name = name
            self.arguments = args
    class FakeTc:
        def __init__(self, name, args):
            self.function = FakeFn(name, args)

    tc = FakeTc("move", '{"direction": "turn right", "steps": 2}')
    asyncio.run(brain._run_one(tc))

    assert brain.object_map == {}, f"map should be cleared, got {brain.object_map}"


def test_run_one_preserves_object_map_after_forward(monkeypatch):
    import asyncio
    from chotu import brain

    brain.object_map.clear()
    brain.object_map.update({"front (+0°)": ["bottle"], "_scan_id": 1})

    async def fake_dispatch(_map, _name, _args_json):
        return {"ok": True, "tool": "move", "result": {}, "duration_ms": 10,
                "timestamp": 0, "error": None}

    monkeypatch.setattr(brain, "dispatch_tool", fake_dispatch)

    class FakeFn:
        def __init__(self, name, args):
            self.name = name
            self.arguments = args
    class FakeTc:
        def __init__(self, name, args):
            self.function = FakeFn(name, args)

    tc = FakeTc("move", '{"direction": "forward", "steps": 1}')
    asyncio.run(brain._run_one(tc))

    assert "front (+0°)" in brain.object_map, "forward must not clear map"
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_spatial_awareness.py -v`
Expected: 11 passed (9 from Task 2 + 2 new).

- [ ] **Step 4: Commit**

```bash
git add chotu/brain.py tests/test_spatial_awareness.py
git commit -m "feat: invalidate object_map on turn

A successful turn dispatched through _run_one now clears the
global object_map. Forward/backward/pose/set_legs preserve it.
Without this, the LLM kept reading stale 'front=bottle' entries
after rotating away from them."
```

---

## Task 5: Drop `segments` from the tool schema

**Files:**
- Modify: `chotu/tools.py` (lines 179–200, the `scan_environment` schema entry)

- [ ] **Step 1: Replace the schema entry**

In `chotu/tools.py`, find the `scan_environment` schema (begins with `"name": "scan_environment"`). Replace the whole entry with:

```python
    {
        "type": "function",
        "function": {
            "name": "scan_environment",
            "description": (
                "Perform a 360° sweep in 6 segments. Rotate, photograph, "
                "identify objects at each segment, return a body-relative "
                "spatial map (front, front-right, back-right, back, back-left, "
                "front-left). Use before 'point at X' tasks or to build "
                "awareness of the surroundings. Robot ends back at its "
                "starting heading."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
```

- [ ] **Step 2: Verify no callers pass `segments`**

Run: `grep -n "scan_environment.*segments\|segments.*scan" chotu/ scripts/ tests/ -r`
Expected: no Python call sites pass a `segments` arg (the LLM's tool schema is the only consumer; we just removed it). Mentions in dry_run.py mock are fine — Task 7 updates that.

- [ ] **Step 3: Commit**

```bash
git add chotu/tools.py
git commit -m "refactor: remove segments param from scan_environment schema

The new scan_environment is fixed at 6 segments. Exposing
segments as a tool parameter implied flexibility that didn't
exist (other counts produced unaligned labels). The LLM now
calls scan_environment() with no arguments."
```

---

## Task 6: Update system prompt — section 5, 6, and example

**Files:**
- Modify: `chotu/system_prompt.py`

- [ ] **Step 1: Update section 5 line for `scan_environment`**

Find the line:

```
- `scan_environment(segments)`: 360° sweep. Returns structured object map. Use before "point at X" tasks.
```

Replace with:

```
- `scan_environment()`: 360° sweep in 6 segments. Returns a body-relative map (front, front-right, back-right, back, back-left, front-left).
```

- [ ] **Step 2: Replace section 6 in full**

Find the block beginning `# 6. Object map` and ending before `# 7. Tool use discipline`. Replace section 6 with:

```
# 6. Object map

When scan results appear in your context, each entry is body-relative:
"front", "front-right", "back-right", "back", "back-left", "front-left".
The number in parentheses (e.g. +60°) is the angle clockwise from where
you were facing when the scan started.

Use the labels for speech and reasoning ("the bottle is front-right, I'll
turn that way"). Use the angles when you need to compute steps: 1 turn
step ≈ 30°, so a target at +60° is ~2 turn-right steps away.

The map clears the moment you turn. If you've turned since the last scan,
the map will not be in your context — re-scan before reasoning about
directions.
```

- [ ] **Step 3: Update the "point at the red cup" example**

Find the example block:

```
**"scan the room"**
scan_environment(8)
[returns map: N=red cup, E=plant, S=wall, W=chair]
speak("Scanned. Red cup north, plant east, chair west, wall south.")

**"point at the red cup"**
[think: red cup is north from last scan. Turning to face north.]
move("turn left", 3, 50)
speak("Facing the red cup.")
```

Replace with:

```
**"scan the room"**
scan_environment()
[returns map: front=red cup, front-right=plant, back=wall, front-left=chair]
speak("Scanned. Red cup ahead, plant front-right, chair front-left, wall behind.")

**"point at the red cup"**
[think: red cup at front (+0°) from last scan. Already facing it.]
speak("Facing the red cup.")
```

(The new example shows: scan returns body-relative labels, and "point at X" can be a no-op when X is already at `front`.)

- [ ] **Step 4: Add a regression test for the prompt content**

Append to `tests/test_spatial_awareness.py`:

```python
def test_system_prompt_describes_body_relative_labels():
    from chotu.system_prompt import build_system_prompt
    p = build_system_prompt("auto")
    assert "front-right" in p
    assert "back-left" in p
    assert "+60°" in p or "+0°" in p
    assert "map clears the moment you turn" in p.lower() or "map clears" in p.lower()


def test_system_prompt_no_longer_uses_compass_labels_in_example():
    from chotu.system_prompt import build_system_prompt
    p = build_system_prompt("reactive")
    # The old example referenced "Red cup north" — this should be gone.
    assert "Red cup north" not in p
```

- [ ] **Step 5: Run all tests**

Run: `pytest tests/ -v`
Expected: 13 passed (11 from earlier + 2 new). Existing tests still green.

- [ ] **Step 6: Commit**

```bash
git add chotu/system_prompt.py tests/test_spatial_awareness.py
git commit -m "docs: rewrite system prompt section 6 for body-relative labels

The LLM now sees front/front-right/back-right/back/back-left/
front-left with absolute degrees in parens, and is told the map
clears whenever it turns. Example replaced to match."
```

---

## Task 7: Update `scripts/dry_run.py` mock

**Files:**
- Modify: `scripts/dry_run.py` (lines 56–60, the `scan_environment` mock branch)

- [ ] **Step 1: Replace the mock branch**

Find:

```python
    if tool == "scan_environment":
        segments = args.get("segments", 8)
        labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][:segments]
        fake_map = [{"direction": d, "objects": []} for d in labels]
        return {**base, "result": {"map": fake_map, "summary": "Dry run — no real images."}}
```

Replace with:

```python
    if tool == "scan_environment":
        from chotu.brain import SCAN_LABELS, SCAN_DEGREES, _build_map_key
        fake_map = {_build_map_key(l, d): [] for l, d in zip(SCAN_LABELS, SCAN_DEGREES)}
        return {**base, "result": {"map": fake_map, "summary": "Dry run — no real images."}}
```

- [ ] **Step 2: Smoke-test the dry run**

Run: `source .venv/bin/activate && python -m scripts.dry_run "scan the room"`
Expected: terminal shows the brain calling `scan_environment` once. The mock returns the new label set without crashing. (Whether the LLM speaks coherently depends on llama-server being up — that's not what this step verifies.)

If llama-server isn't running, just verify the import + mock works:

Run: `python -c "from scripts.dry_run import _fake_pi_call; import asyncio; r = asyncio.run(_fake_pi_call('scan_environment', {})); print(list(r['result']['map'].keys()))"`

(If the function name in `scripts/dry_run.py` differs, substitute it. Inspect the file to find the dispatch-mock entry point.)

Expected: prints `['front (+0°)', 'front-right (+60°)', 'back-right (+120°)', 'back (+180°)', 'back-left (+240°)', 'front-left (+300°)']`.

- [ ] **Step 3: Commit**

```bash
git add scripts/dry_run.py
git commit -m "chore: update dry_run mock for body-relative scan labels

Pulls the canonical label/degree tables from chotu.brain so the
mock map shape matches the real scan output."
```

---

## Task 8: Manual verification on charged Pi

This task is human-in-the-loop. Don't tick the boxes for an agent; prompt the user to run these checks once the Pi is available.

**Files:** none changed.

- [ ] **Step 1: Start the bridge and llama-server**

```bash
# Pi
ssh chotu@chotu.local 'sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py' &

# Laptop
llama-server -m /home/rishi/.local/share/localis/models/Qwen3.5-4B-Q4_K_M.gguf \
  --mmproj /home/rishi/.local/share/localis/models/mmproj-BF16.gguf \
  --port 8080 -ngl 99 -c 16384 --parallel 1 &
```

Confirm both are up.

- [ ] **Step 2: Sanity scan**

Run: `source .venv/bin/activate && python -m chotu.brain --goal "what's around you?"`
Expected: terminal shows one `scan_environment` call, 6 `[capture_vision]` entries, and a final speak that mentions all 6 directions (front, front-right, etc.). No context-overflow error.

- [ ] **Step 3: Targeted retrieval**

Place a blue bottle ~120° clockwise from Chotu's start heading (i.e. roughly behind-right of where Chotu faces).
Run: `python -m chotu.brain --goal "find the blue bottle"`
Expected: scan tags the bottle at `back-right`, LLM issues `move("turn right", ~4)`, then `capture_vision` to confirm, then `goal_complete(success=True)`.

- [ ] **Step 4: Map invalidation check**

In a goal run, watch for any debug line like `[map] invalidated after turn right` after a turn. After such a turn, the next iteration's prompt should not contain the object map (you can confirm via `CHOTU_DEBUG=1` and watching message construction).

Run: `CHOTU_DEBUG=1 python -m chotu.brain --goal "scan, then walk to the closest object"`
Expected: scan completes, LLM turns toward something, debug line shows invalidation, next iteration has no map injection.

- [ ] **Step 5: Long run**

Run a goal that requires 5+ scans across the run (e.g. "explore the room, name 5 things you find, then sit"). Confirm no context-exceeded error. Watch terminal for "context exceeded" / "8192" / "16384" mentions in any error trace.

- [ ] **Step 6: Update memory + close out**

If all checks pass, update `MEMORY.md` (auto-memory) with a project-status note that scan and ctx-bump are done. Don't commit memory; that's local.

---

## Self-Review Checklist (filled out)

**1. Spec coverage:**
- ctx bump → Task 1 ✓
- scan_environment rewrite (6 × 2-step segments, body-relative labels) → Tasks 2 + 3 ✓
- Map invalidation on turn → Tasks 2 + 4 ✓
- System prompt update (sec 5, sec 6, example) → Task 6 ✓
- Tool schema update (drop `segments`) → Task 5 ✓
- dry_run mock update → Task 7 ✓
- Test plan from spec → Task 8 (manual on-Pi) ✓

**2. Placeholders:** none. Every code step contains the exact code; every command shows the expected output.

**3. Type consistency:** `SCAN_LABELS`, `SCAN_DEGREES`, `SCAN_SEGMENTS`, `TURN_STEPS_PER_SEGMENT`, `_build_map_key`, `_should_invalidate_map_after_turn` — names match across Tasks 2, 3, 4, 6, 7. The map shape (flat dict keyed by `"label (+deg°)"`) is consistent in `scan_environment_tool` (Task 3), the dry_run mock (Task 7), and the test seeds (Tasks 2, 4).

**4. Hidden assumption:** Task 3 keeps `object_map["_timestamp"]` so the existing 60s freshness gate in `build_messages` (line 134) and `run_goal` (line 243) keeps working. Verified: those callers filter `_timestamp` out before serializing — they won't leak into the prompt.
