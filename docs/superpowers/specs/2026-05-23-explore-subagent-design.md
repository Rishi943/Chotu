# Explore Subagent + Heartbeat Hygiene — Design

**Date:** 2026-05-23
**Status:** Draft, pending implementation
**Related:** `2026-05-23-explore-habit-design.md`, `2026-05-22-monologue-heartbeat-design.md`

## Problem

Today's live session (monologue-heartbeat branch) surfaced four interacting issues during a real explore + idle session on Chotu:

1. **Explore tools polluted the main Chotu loop.** A 4B local model holding PALIV.md + CHOTU.md + HEARTBEAT.md + explore semantics simultaneously forgot rules mid-scan (called `get_perception` and `explore()` while already in explore scope), and lost track of progress.
2. **Heartbeat context bloat → identity-loop.** 25+ consecutive heartbeats produced near-identical monologues ("still white fabric / still no blue bottle"). Each tick appended to history; nothing evicted; the model pattern-matched into more of the same. Eventually a 30-call `turn_right` runaway needed Ctrl+C.
3. **`record_photo` guard over-suppresses.** One failed `record_photo` suppressed every subsequent call for the rest of the turn — even with different (valid) args — silently dropping photos 5–12 of a scan.
4. **`open_path` "one per node" misfires.** Rule conflated "node" with "scan-photo-set". Each of the ~10 scan photos faces a different heading, so each should be allowed to declare its own exit; today only the first one wins.

Plus two calibration items:
- Full 360 on Chotu takes ~10 single `turn_right` steps, not 12.
- Context (`-c 16384`) blew mid-scan at 16456 tokens.

## Goals

- Isolate explore from Chotu so the small model only holds one prompt's worth of rules at a time.
- Stop heartbeat-driven identity loops without adding loop-detection machinery.
- Build a shared, persistent map module so future Chotu features can navigate by place.
- Fix the two scan bugs that made today's explore unusable past photo 4.

## Non-goals

- `recall_place(query)` / object-by-name lookup — deferred, comes after explore is solid.
- Multi-process or multi-model setups — explore subagent runs in the same llama-server.
- Heartbeat interrupts *into* the explore subagent — explore runs synchronously, no heartbeats during it.
- SQLite or any database (per CLAUDE.md). World state is JSON on disk.

## Architecture

```
┌─ core/brain.py (parent Chotu loop) ────────────────────┐
│  - PALIV.md + CHOTU.md system prompt                   │
│  - Chotu tools: speak, move, get_perception, ...       │
│  - explore() tool → blocking call to subagent          │
│  - Heartbeat ticks (5-msg rolling window)              │
└────────────────────┬───────────────────────────────────┘
                     │ explore() invoked
                     ▼
┌─ core/explore_agent.py (subagent, sync) ───────────────┐
│  - EXPLORE.md system prompt only (no PALIV/CHOTU)      │
│  - Explore tools only                                  │
│  - No heartbeat injection                              │
│  - Per-node turn fuse; node count cap                  │
│  - Writes to core/world.py shared state                │
└────────────────────┬───────────────────────────────────┘
                     │ on conclude / cap hit
                     ▼
   returns {status, nodes_added, anchors_seen, message}
                     │
                     ▼
┌─ core/world.py (shared map state) ─────────────────────┐
│  - In-memory graph: nodes, photos, exits               │
│  - Persisted to data/world.json on every write         │
│  - Loaded on brain.py startup                          │
└────────────────────────────────────────────────────────┘
```

### Files

**New:**
- `core/explore_agent.py` — subagent loop
- `core/world.py` — shared map module
- `EXPLORE.md` — subagent system prompt
- `data/world.json` — persistent map (gitignored)

**Modified:**
- `core/brain.py` — replace inline explore handling with `explore()` tool that awaits subagent; heartbeat sliding-window eviction; per-args fail guard
- `core/prompts.py` — drop HEARTBEAT.md from system prompt; `SYSTEM_PROMPT = PALIV.md + CHOTU.md`
- `core/tools.py` — remove explore-scope tools from main registry; keep `explore()` entry tool
- `CHOTU.md` — new "Heartbeats" section with good/bad examples
- `.env` (sample) — add `PALIV_EXPLORE_TURNS_PER_REVOLUTION=10`, bump llama-server `-c` to 32768 in docs

**Deleted:**
- `HEARTBEAT.md` (stale, content folded into CHOTU.md)

## Component: `core/world.py`

In-memory graph, JSON persistence, single-writer (subagent only writes; parent reads).

### Data shape (`data/world.json`)

```json
{
  "nodes": {
    "node-001": {
      "id": "node-001",
      "x": 0, "y": 0,
      "heading_at_scan_start": 0,
      "anchors": ["carpet", "green wall"],
      "objects": ["bare foot", "blue bottles"],
      "photos": [
        {
          "photo_idx": 0,
          "heading": 0,
          "description": "...",
          "anchors_in_photo": ["carpet"],
          "objects_in_photo": ["bare foot"],
          "open_path": true,
          "forward_steps": 4,
          "distance_cm": 50
        }
      ],
      "exits": [
        {"heading": 0, "to_node": "node-002", "forward_steps": 4}
      ],
      "created_at": 1716501234
    }
  },
  "origin_node": "node-001",
  "version": 1
}
```

`heading` is degrees, integer. With 10 photos per revolution, headings are multiples of 36 (0, 36, 72, ...). Photos at heading H replace any prior photo at the same H (last write wins within a scan).

### Module surface

```python
def load() -> None             # called once at brain.py startup
def save() -> None             # called after every mutation
def add_node(x: int, y: int, heading: int) -> str   # returns node_id
def add_photo(node_id, photo_idx, heading, description, anchors_in_photo,
              objects_in_photo, open_path, forward_steps=None, distance_cm=None)
def add_exit(from_node: str, heading: int, to_node: str, forward_steps: int)
def get_node(node_id: str) -> dict
def list_nodes() -> list[dict]
def origin() -> str            # returns origin node_id
```

No locking — single Python process, subagent runs synchronously inside brain.py's event loop.

## Component: `core/explore_agent.py`

```python
async def run_explore(reason: str = "idle") -> dict:
    """Run the explore subagent to completion. Returns summary envelope."""
```

### Loop

1. Build message list: `[{"role": "system", "content": EXPLORE_PROMPT}]`. No user message, no parent history.
2. Until termination:
   a. Call llama-server with `EXPLORE_TOOLS` schema, `enable_thinking=False`.
   b. Dispatch each tool call, append assistant + tool results to local messages.
   c. **Image eviction:** after `record_photo` succeeds, walk back over prior `capture_vision` tool results *in the same node's scan* and replace JPEG bytes with `[image evicted — see record_photo description]`.
   d. Per-args fail guard (same as parent fix in §Bugfixes): suppress repeat of `(tool_name, sorted_kwargs)` within one turn only.
   e. Check caps.
3. Return envelope.

### Termination

| Trigger | Action | Return status |
|---|---|---|
| Model calls `conclude(notes)` | auto-run `return_to_origin` | `"done"` |
| `MAX_NODES` (default 5) reached | auto-run `return_to_origin` | `"cap_nodes"` |
| Per-node fuse `MAX_TURNS_PER_NODE` (default 30) hit | force `commit_node_and_advance`; if that fails, abort | `"node_fuse"` |
| LLM error / context overflow | log, return | `"error"` |

No global turn cap — per-node fuse + node cap bounds total work without artificially halting a productive session.

### Return envelope

```python
{
  "status": "done" | "cap_nodes" | "node_fuse" | "error",
  "nodes_added": ["node-001", "node-002"],
  "anchors_seen": ["carpet", "green wall", "white sheet"],
  "message": "Mapped 2 nodes from origin. Notable: cup at node-001, sheet pile at node-002."
}
```

The `message` field is a short LLM-generated summary (the subagent's `conclude(notes)` text, or an auto-summary on cap exits). Parent Chotu sees only this envelope.

## Component: `EXPLORE.md`

System prompt for the subagent. Terse, mechanical, no persona. Target ~150 lines.

### Outline

1. **Role:** "You are a mapping subroutine. Build a navigation graph of the space. Do not look for specific objects."
2. **Tools available** — the six tools listed below, each with one-line semantics.
3. **Scan protocol:**
   - At each node: take 10 photos, one per 36° turn.
   - For each photo: `capture_vision()` → `record_photo(...)`.
   - Mark `open_path=true` on photos where the floor is clear forward.
   - After 10 photos you are back to your starting heading.
   - Then call `commit_node_and_advance()`.
4. **Movement protocol:** in explore scope, `move` only accepts `turn_left`/`turn_right` with `steps=1`. Forward motion happens only via `commit_node_and_advance`.
5. **Termination:** when you've mapped enough (3–5 nodes) or every direction is a dead end, call `conclude(notes="...")`. Don't try to find specific objects.
6. **Failure modes:**
   - If a tool returns an error, read the error and fix the args. Do **not** repeat the same call.
   - If `commit_node_and_advance` returns `advanced: false`, this node has no open exits — call `conclude`.
7. **Worked example:** one full node scan (10 photos with anchors), then advance, then second node, then conclude.

### Tool surface (subagent only)

| Tool | Semantics |
|---|---|
| `capture_vision()` | JPEG → multimodal user message with description |
| `record_photo(anchors, objects, description, open_path, forward_steps?, distance_cm?)` | Log photo at current heading; writes to world.py |
| `move(direction, steps)` | Restricted: `turn_left` or `turn_right`, `steps=1` only |
| `commit_node_and_advance()` | Closes scan, advances forward if any photo had `open_path=true`; returns `{advanced: bool, new_node_id?}` |
| `return_to_origin()` | Pathfind back to origin along recorded edges |
| `conclude(notes)` | Finish explore; subagent auto-calls `return_to_origin` |

None of these are exposed to the parent Chotu loop. Parent only sees `explore()`.

## Component: Heartbeat changes

### Sliding window (`core/heartbeat.py` + `core/brain.py`)

- Each appended message gets an internal `_origin` field: `"heartbeat" | "user" | "boot"`.
- Field is stripped before sending to the LLM.
- At the start of each heartbeat tick, walk `messages`, drop oldest heartbeat-tagged blocks (assistant message + its tool calls + tool results) until ≤5 heartbeat blocks remain.
- User-initiated turns and their replies are never evicted (manage user message history separately, with existing limits).

### CHOTU.md additions

Add a "Heartbeats" section near the existing voice/personality content:

```markdown
## Heartbeats

A `[heartbeat]` ticks every few seconds when nothing else is happening.
You see the last 5 heartbeats in context. Use them to notice when you're
stuck in your own head and break out.

### Good — notices the loop on the 4th tick
[heartbeat] *Still that white sheet. Same fold pattern.*
[heartbeat] *White sheet again. Nothing new.*
[heartbeat] *Same view. White sheet.*
[heartbeat] → calls move(turn_right, 1)
  *Three ticks of the same thing. Time to actually look elsewhere.*

### Bad — four identical observations, no action
[heartbeat] *Still white fabric. Still that pink wall.*
[heartbeat] *White fabric. Pink wall.*
[heartbeat] *Same white fabric.*
[heartbeat] *Still white fabric, still pink wall.*
  ← you are looping. Don't do this. After 3 similar ticks, change
    something — move, capture, or stay silent (no monologue at all is fine).
```

Plus one-liner in the heartbeat rules: *"If your last 3 heartbeats said roughly the same thing, on the next tick either take an action or output nothing."*

### Prompt loader (`core/prompts.py`)

```python
SYSTEM_PROMPT = PALIV_MD + "\n\n" + CHOTU_MD   # HEARTBEAT_MD removed
```

Delete `HEARTBEAT.md` from the repo.

## Bugfixes (bundled)

### Fix 1 — Per-args fail guard

**Today:** if `record_photo` fails once in a turn, every subsequent `record_photo` is suppressed regardless of args.

**Fix:** key the suppression set on `(tool_name, hash(sorted_kwargs))` instead of `tool_name`. Located in `core/brain.py` (and mirrored in `core/explore_agent.py`).

### Fix 2 — `open_path` per-heading, not per-node

**Today:** `world.py` (or current scope state) rejects `open_path=true` on any photo after the first one in a node.

**Fix:** allow `open_path=true` on photos at distinct headings. Reject only if a photo at the *same* heading already has it set. Each node can have multiple exits (one per direction with a clear path).

### Both fixes get unit tests

- `tests/test_guard.py::test_allows_different_args` — record_photo with args A fails, then args B succeeds in same turn.
- `tests/test_world.py::test_open_path_per_heading` — set `open_path=True` on photos at headings 0, 36, 72 — all succeed; repeating heading 0 fails.

## Context window

- Bump llama-server launch: `-c 16384` → `-c 32768`. Update CLAUDE.md "Dev setup" line.
- Image eviction in subagent (already covered in §explore_agent loop).
- Parent Chotu eviction: when stripping old heartbeat blocks (sliding window), JPEG bytes in their tool results are dropped with them automatically.

## Calibration

- New env var `PALIV_EXPLORE_TURNS_PER_REVOLUTION` (default `10`). Read by `explore_agent.py`. Documented in `.env.example` and CLAUDE.md.
- Used by EXPLORE.md prompt-template substitution: "take {N} photos, one per turn".

## Error handling

- **Subagent LLM error** → log, return `{"status": "error", "message": "..."}`. Parent treats this as a tool error, surfaces a short Chotu-voice apology.
- **Pi unreachable mid-scan** → `move` tool returns error envelope; per-args guard prevents tight retry loop; subagent's prompt tells it to call `conclude` if movement keeps failing.
- **`world.json` write fails** → log, continue with in-memory state; the next successful write will retry.
- **Corrupt `world.json` on load** → log, start with empty graph (don't crash brain.py).

## Testing

| Layer | Test | Asserts |
|---|---|---|
| Unit | `test_world.py` | add_node assigns sequential ids; save/load roundtrip; add_photo at same heading replaces; add_exit dedups |
| Unit | `test_guard.py` | per-args suppression key |
| Unit | `test_open_path.py` | per-heading allowed, same-heading rejected |
| Integration | `scripts/test_explore_dry.py` | run subagent against faked Pi returning canned vision; assert `status="done"`, ≥1 node in `world.json`, last move returns subagent to origin |
| Manual | one Pi explore | inspect `data/world.json`, eyeball return-to-origin, no Ctrl+C needed |

## Rollout

1. World module + tests (no behavior change to brain).
2. Subagent + EXPLORE.md (still un-wired).
3. Switch parent `explore` tool to dispatch subagent; remove explore tools from main registry.
4. Heartbeat sliding window + CHOTU.md examples; delete HEARTBEAT.md; update prompts.py.
5. Bugfixes + their tests.
6. Bump `-c`; smoke test on Pi.

Each step is independently committable; if a step breaks the live loop, the previous commit is the safe rollback point.

## Open items deliberately left out

- `recall_place(query)` and any object-by-name lookup. Comes after explore is solid.
- Cross-node anchor reconciliation (knowing the "green wall" at node-1 is the same as at node-3). Deferred.
- Heartbeat interrupts into the running subagent (e.g., wake word during explore). Deferred — for now, explore runs to completion before parent resumes.
