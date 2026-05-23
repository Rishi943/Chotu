# Explore Habit — Design Spec

**Date:** 2026-05-23
**Branch:** monologue-heartbeat
**Status:** Approved (pre-implementation)
**Supersedes:** the WorkflowAgent design from `2026-05-22-workflow-agent-investigate-design.md` — see "Relationship to investigate spec" below.

---

## Problem

Chotu has no spatial awareness of the room beyond what shows up in the current camera frame. We want it to be able to choose, on its own initiative, to map the room as a chain of vantage points ("nodes") and return a structured map it can later use to find specific objects via the `investigate` habit (separate spec).

The map must:
- Be built without IMU/gyro — only ultrasonic + camera + step-count tracking.
- Preserve per-photo detail (each photo has its own anchors/objects/heading) so `investigate` can later say "the cup was seen at node 1, x=4, face that direction."
- Guarantee a reversible navigation chain — at the end of explore, Chotu must walk back to Node 0.
- Survive in Chotu's working memory as one structured tool result. All intermediate scan messages must be dropped after the habit concludes.

`explore` is invoked when Chotu's LLM decides to call the tool — it is not auto-fired on boot, not picked by a scheduler.

---

## Design summary

`explore` is a tool the LLM calls with no parameters. Dispatch opens an **execution scope** — a marker in the main brain's `_process` loop that:

1. Injects `workflows/explore.md` as a single user message into the live conversation (so `PALIV.md + CHOTU.md + HEARTBEAT.md` stay loaded — KV cache prefix is untouched).
2. Swaps in a scope-specific tool surface: a small set of scope-only tools, with `move` restricted, and other habit tools blocked.
3. Tags every message added during the scope with `scope_id`.
4. When the LLM calls `conclude(...)`, splices `memory[]` to remove all scope-tagged messages and replaces them with one synthetic `tool` result for the original `explore` call carrying the assembled map JSON.

After conclude, Chotu's main message thread is `prior_history + tool_result(map)`. KV cache hits through `prior_history`; only the synthetic result needs to be prefilled before the next turn.

There is **no separate LLMClient, no sub-agent, and no second message thread.** This is a deliberate departure from the earlier investigate spec — see "Relationship to investigate spec."

---

## Heading and node frame

- Each node has a local frame. `x0` = the heading the robot was facing when it arrived at that node. `x1..x11` = headings after 1..11 right-turn steps (≈ 30° each).
- **Right turn = +1 x. Left turn = −1 x. Math is modulo 12.**
- Each node holds 12 photos, one per x index.
- Python tracks `current_x` for the active node — the LLM never computes or passes x.
- An edge from Node N to Node N+1 is encoded as `(open_path_x, forward_steps)` stored on the open-path photo of Node N.
- Return recipe (executed by `return_to_origin`, not the LLM):
  ```
  for node in reversed(path_stack):
      target_x = node.open_path_x      # x at this node pointing to next-younger node
      # We arrived back at this node facing x((target_x + 6) % 12). Re-orient:
      delta = (target_x - current_x) % 12
      move("turn right", delta, speed=80)   # always non-negative; delta=0 is a no-op
      # Now facing the original exit direction. Reverse it:
      move("turn right", 6, speed=80)       # 180°
      move("forward", node.forward_steps, speed=80)
      # Arrived at the previous (parent) node.
  ```

Lossy-by-design: the absolute compass orientation is never tracked. Map is a chain of local frames. That is sufficient for `investigate` because investigate also navigates within these local frames.

---

## Architecture

```
main brain (_process):

  LLM calls explore()
    → tools.dispatch("explore") opens a Scope:
        - scope_id = uuid
        - tool_call_id of the originating explore call (for the splice later)
        - state: { current_node_id=0, current_x=0, nodes=[], path_stack=[], failed_advances=0 }
        - tool surface: SCOPE_TOOLS (capture_vision, move-restricted,
                                     record_photo, commit_node_and_advance,
                                     return_to_origin, conclude,
                                     get_distance, get_battery, set_face,
                                     speak, wait)
    → memory.append(synthetic user msg: contents of workflows/explore.md,
                    tagged scope_id)
    → return: control flows back into the SAME _process loop, no tool result
              is appended yet — the explore call is "open" until conclude fires.

  LLM loops on its own using SCOPE_TOOLS. Each assistant message + each tool
  result message is tagged with scope_id.

  When LLM calls conclude(status, notes):
    → build map from scope.state.nodes
    → memory[]: drop all messages tagged scope_id.
                The original assistant message that contained the explore
                tool_call is NOT tagged (it was appended before scope opened)
                and is kept in place.
    → append: tool msg with tool_call_id = originating_tool_call_id,
              content = JSON-serialised map. Untagged.
    → close scope
    → _process continues normally; next inference sees:
        prior_history → assistant(monologue + tool_call(explore))
                      → tool(map JSON)
      All intermediate scan turns are gone. Warm prefix cache hits through
      prior_history; only the assistant+tool messages need fresh prefill.
```

`tool_chain_active` (set while `_process` runs) keeps heartbeats suppressed for the whole scope duration. Human input queues — explore runs uninterrupted (same as the investigate spec; interruption is a future concern).

---

## Components

### `core/scope.py` (new)

```python
@dataclass
class ExploreState:
    current_node_id: int = 0
    current_x: int = 0
    nodes: list[dict] = field(default_factory=list)   # accumulated per-node dicts
    current_node_photos: list[dict] = field(default_factory=list)
    current_node_open_path: dict | None = None        # {x, forward_steps} or None
    path_stack: list[dict] = field(default_factory=list)  # [{from_node, open_path_x, forward_steps}, ...]
    failed_advances: int = 0
    returned_to_origin: bool | None = None            # filled by return_to_origin

@dataclass
class Scope:
    scope_id: str
    originating_tool_call_id: str
    originating_tool_name: str    # "explore" (later "investigate")
    state: ExploreState
    tool_surface: dict            # name -> async callable
    tagged_message_indexes: list[int]
```

Helpers:
- `open_scope(brain, tool_call_id, tool_name) -> Scope` — installs scope, returns it.
- `close_scope(brain, scope, final_result: dict) -> None` — performs the message splice described above.

One scope active at a time (`brain.active_scope: Scope | None`). Nesting is rejected.

### `core/explore_tools.py` (new)

Async tool functions, each takes `(pi, scope)` and returns a standard envelope. The scope-local dispatch map wraps them with the right signatures.

- `capture_vision(pi, scope)` — wraps the existing `capture_vision_tool`. Result envelope's `result` dict gains `current_x: int`.
- `move(pi, scope, direction, steps=1)` — **restricted, no `speed` parameter exposed to the LLM**:
  - Python always passes `speed=80` to `pi.move(...)`. The LLM cannot set or override it.
  - `direction == "turn left"` or `"turn right"`: must have `steps == 1`. Updates `scope.state.current_x` (`+1` for right, `−1` for left, mod 12). Returns `{current_x}`.
  - Any other direction or `steps != 1`: returns error envelope `"move restricted in explore scope: only single turn-left/turn-right steps allowed; use commit_node_and_advance for forward motion."` — does **not** touch the Pi.
- `record_photo(scope, anchors, objects, description, open_path=False, forward_steps=None)`:
  - Appends `{x: scope.state.current_x, anchors, objects, description, open_path, forward_steps}` to `current_node_photos`.
  - If `open_path=True`: requires `forward_steps` to be a positive int, errors if `current_node_open_path` is already set.
  - Returns `{recorded: true, photos_so_far: len(current_node_photos)}`.
- `commit_node_and_advance(pi, scope)`:
  - Builds the node dict (id, anchors_summary as deduped union of per-photo anchors, photos) and appends to `state.nodes`.
  - If `current_node_open_path` is None → terminal node. Return `{advanced: false}`. State retained as the last node; subsequent records will be rejected ("commit a return_to_origin or conclude next").
  - Else: attempt the exit move atomically. All Pi moves below use `speed=80` (hardcoded; LLM has no influence).
    1. Turn from `current_x` to `open_path_x` via shortest-side (Python picks right or left).
    2. `pi.get_distance()` — if obstacle < 15cm, treat as failure (don't walk).
    3. Otherwise `pi.move("forward", forward_steps, speed=80)`.
    4. If the envelope reports ok and no obstacle mid-walk: push `{from_node, open_path_x, forward_steps}` onto `path_stack`, increment `current_node_id`, reset `current_x=0`, reset `current_node_photos=[]`, `current_node_open_path=None`. Return `{advanced: true, new_node_id: ...}`.
    5. **On failure** (obstacle, bridge error, partial walk): walk back to the current node — `pi.move("backward", steps_taken_before_failure, speed=80)`, then turn back to `current_x` so the LLM resumes its scan state. Clear the open_path on the current node so LLM can pick a new one. Increment `state.failed_advances`.
    6. If `state.failed_advances >= 3` (global count across the run): force-end — automatically call `return_to_origin()` then return `{advanced: false, aborted: true, reason: "3 advance failures"}`. The LLM will see this and is expected to call `conclude(status="inconclusive", ...)`.
- `return_to_origin(pi, scope)`:
  - Walks `path_stack` in reverse using the recipe above. On each step:
    - Check ultrasonic before each forward move.
    - On unrecoverable failure: stop, set `state.returned_to_origin=False`, return `{success: false, last_node_reached: <id>, error: "..."}`.
  - On full success: `state.returned_to_origin=True`, return `{success: true, last_node_reached: 0}`.
- `conclude(scope, status, notes)`:
  - Validates `status ∈ {"done", "inconclusive"}`.
  - Builds map JSON (see schema below).
  - Calls `close_scope` to splice memory and append the synthetic tool result.

### `core/habits.py` — `explore` entry

```python
async def explore(pi: PiClient, brain: "Brain", tool_call_id: str) -> None:
    """Habit entry. Opens an explore scope and returns control to the brain loop.

    Does not return a tool result directly — the result is appended later by
    conclude → close_scope. _process must understand "no result this turn,
    keep looping under the scope."
    """
    workflow_doc = (REPO_ROOT / "workflows" / "explore.md").read_text(encoding="utf-8")
    scope = open_scope(brain, tool_call_id=tool_call_id, tool_name="explore")
    brain.memory.append({"role": "user", "content": workflow_doc, "_scope_id": scope.scope_id})
    scope.tagged_message_indexes.append(len(brain.memory) - 1)
    # No return value to append as a tool result — _process detects scope is
    # open and skips appending the tool_result for "explore" until conclude.
```

### `core/brain.py` — `_process` changes

Two surgical changes:

1. **Scope-aware tool dispatch.** Inside the LLM call loop, when a tool call arrives:
   - If `brain.active_scope`: look up the tool in `scope.tool_surface` first; fall back to global dispatch only for explicitly allowed names (set: `get_distance`, `get_battery`, `set_face`, `speak`, `wait`, `capture_vision`). Anything else returns a structured error envelope ("not available in explore scope") — no exception.
   - Every assistant message and every tool result added during scope is tagged via a side-list `scope.tagged_message_indexes`.

2. **Scope-opening tools don't append a tool result on the same turn.** Normal tools return an envelope which is appended as the `tool` message for that `tool_call_id`. `explore` and (later) `investigate` are scope-openers — they install the scope and append the workflow doc instead. `_process` must skip appending the tool result for the originating tool_call_id and continue looping.

3. **`close_scope` performs the splice.** When `conclude` is called:
   - Sort `tagged_message_indexes` descending, remove each from `memory`.
   - Append the synthetic tool result for the originating tool_call_id (this is what main Chotu sees as the explore output).
   - Clear `brain.active_scope`.

### `core/tools.py` — schema addition

```json
{
  "name": "explore",
  "description": "Map the room you're in as a chain of vantage points. Returns a structured map with per-photo anchors and objects, suitable for later use by investigate. Long-running (minutes). Call when you have no spatial awareness or have moved to a new space.",
  "parameters": { "type": "object", "properties": {}, "required": [] }
}
```

No `conclude` schema in the global TOOL_SCHEMAS — only injected into the scope surface.

### `workflows/explore.md` (new, repo root)

Skill-doc style (see the v3 sketch in brainstorm content). Final text is written as part of implementation; the sketch is the contract.

---

## Map schema (the `conclude` result)

```json
{
  "nodes": [
    {
      "id": 0,
      "anchors_summary": ["air vent", "bed frame", "desk", "nightstand"],
      "photos": [
        {
          "x": 0,
          "anchors": ["bed frame"],
          "objects": ["pillow"],
          "description": "head of bed",
          "open_path": false,
          "forward_steps": null
        },
        {
          "x": 3,
          "anchors": ["desk"],
          "objects": ["laptop", "mug"],
          "description": "desk ahead, floor clear",
          "open_path": true,
          "forward_steps": 8
        }
        // x1, x2, x4..x11 — 12 entries total per node
      ]
    },
    {
      "id": 1,
      "anchors_summary": ["window", "bookshelf"],
      "photos": [ /* 12 entries */ ]
    },
    {
      "id": 2,
      "anchors_summary": ["door frame", "outlet"],
      "photos": [ /* 12 entries; all open_path:false (terminal) */ ]
    }
  ],
  "returned_to_origin": true,
  "node_count": 3,
  "notes": "small bedroom, well-lit"
}
```

`anchors_summary` is built by Python from the per-photo `anchors[]` (deduped, order-preserving). The LLM does not produce it.

---

## Data flow example

```
[heartbeat]
  → LLM: monologue "I should map this room" + tool_call("explore")
  → _do_explore appends workflows/explore.md as user msg (scope-tagged)
  → LLM turn: tool_call(capture_vision)
  → result: {image_base64, current_x: 0} [scope-tagged]
  → LLM turn: monologue "I see a bed frame" + tool_call(record_photo, anchors=["bed frame"], ...)
  → result: {recorded: true, photos_so_far: 1} [scope-tagged]
  → LLM: tool_call(move, "turn right", 1)
  → result: {current_x: 1} [scope-tagged]
  ... repeats for x1..x11 ...
  → LLM: tool_call(commit_node_and_advance)
  → result: {advanced: true, new_node_id: 1} [scope-tagged]
  ... repeats for node 1, then node 2 (terminal) ...
  → LLM: tool_call(return_to_origin)
  → result: {success: true, last_node_reached: 0} [scope-tagged]
  → LLM: tool_call(conclude, status="done", notes="...")
  → close_scope:
      memory[] gets spliced — all scope-tagged messages removed
      synthetic tool result appended for the original explore tool_call_id:
        { ..., result: <full map JSON>, ... }
  → next iteration of _process: LLM sees prior history + clean tool result.
```

---

## Error handling

| Case | Behavior |
|---|---|
| `capture_vision` fails at a turn | Tool returns error envelope. LLM records the photo with `description: "vision failed"`, empty anchors/objects, `open_path: false`. Continues. |
| `move("turn right", 1)` fails | Error envelope returned, LLM may retry. `current_x` only updates on successful move (Python checks the envelope). |
| LLM tries `move("forward", ...)` directly | Restricted-move error returned without touching the Pi. LLM is reminded by the doc to use `commit_node_and_advance`. |
| LLM tries `pose`, `do_trick`, `get_perception`, `investigate`, or another `explore` | Tool dispatch returns `"not available in explore scope"` error envelope. |
| `commit_node_and_advance` fails (obstacle, bridge error) | Walk back to current node, clear open_path, increment `failed_advances`. LLM retries with a different open_path. |
| 3 `commit_node_and_advance` failures total | Force `return_to_origin`, return `{aborted: true}`. LLM expected to call `conclude(status="inconclusive")`. |
| `return_to_origin` fails partway | Stop where you are. `success: false, last_node_reached: N, error: "..."` returned. `state.returned_to_origin = false`. LLM should call `conclude(status="inconclusive")` from wherever it ended up. |
| Pi unreachable mid-scope | All Pi calls return error envelopes. LLM concludes inconclusive. |
| LLM never calls `conclude` | Safety cap of 200 LLM iterations inside scope (not advertised). On hit: synthesise `conclude(status="inconclusive", notes="iteration cap reached")` and splice. |
| Battery low event during scope | Same as current `tool_chain_active` policy — event queues until scope closes. (No mid-scope interruption in V1.) |

---

## Testing

`tests/test_explore_scope.py` (new):
- Scope install: `_process` with mocked LLMClient returning a tool_call for `explore` → scope opens, workflow doc injected, scope-tagged.
- `move` restriction: scope-aware dispatch returns error envelope for `move("forward", 1)` without touching Pi mock; `move("turn right", 1)` increments `current_x` and calls Pi mock.
- `record_photo` validation: rejects second `open_path: true` on same node; rejects `open_path: true` with `forward_steps: None`.
- `commit_node_and_advance` happy path: pushes onto `path_stack`, resets `current_x` and `current_node_photos`, increments `current_node_id`.
- `commit_node_and_advance` failure path: increments `failed_advances`, clears open_path, walks back. Three failures auto-trigger `return_to_origin`.
- `return_to_origin`: with mocked Pi returning ok, walks path_stack in reverse, executes the correct turn+forward sequence per edge. Failure case: stops, sets `returned_to_origin=False`.
- `conclude`: splice removes all scope-tagged messages from `memory`; appends one synthetic tool result with the assembled map JSON; `brain.active_scope` is None after.

No on-Pi automated tests for this spec. End-to-end behavior verified manually on the bot.

---

## Relationship to investigate spec

The earlier spec (`2026-05-22-workflow-agent-investigate-design.md`) used a separate `WorkflowAgent` with its own `LLMClient` and message thread. That design re-prefills the KV cache on every habit invocation because the system prompt is different.

This spec replaces it. **Investigate will migrate to the same scoped-execution model** when it's implemented. The investigate tool will:
- Open a scope tagged `investigate` (not `explore`)
- Inject `workflows/investigate.md`
- Use the same `record_photo` / `commit_*` style helpers shaped to investigate (TBD in a follow-up spec)
- Conclude through the same splice mechanism

Either way, the scope infrastructure (`core/scope.py`, brain `_process` changes) is built once and reused.

---

## Files summary

| Path | Action |
|---|---|
| `core/scope.py` | Create |
| `core/explore_tools.py` | Create |
| `core/habits.py` | Modify — add `explore(pi, brain, tool_call_id)` entry |
| `core/brain.py` | Modify — scope-aware dispatch, scope-tagging, splice on close |
| `core/tools.py` | Modify — add `explore` schema; route dispatch through scope opener |
| `workflows/explore.md` | Create |
| `tests/test_explore_scope.py` | Create |

---

## Out of scope

- `get_map` tool for retrieving a persisted prior map across calls. Flagged as follow-up. For V1, calling `explore` a second time produces a fresh map; the previous one survives only as long as it stays in the rolling memory window.
- `investigate` migration to scoped execution — separate spec, after explore lands.
- Branching/looping graphs (Node 3 → Node 0 shortcut). Linear chain only.
- IMU/odometry. Pure step-count + ultrasonic.
- Cm-per-step calibration. Map carries step counts only.
- Mid-scope user-input interruption. Queues until conclude (same as investigate spec).
- Distance/space estimates in the map (e.g. "node 1 is ~2.4m from node 0"). Just steps.
