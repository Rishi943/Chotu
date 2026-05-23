# Workflow Sub-Agent + Investigate Redesign

**Date:** 2026-05-22
**Branch:** monologue-heartbeat
**Scope:** Universal workflow sub-agent architecture + `investigate` as first consumer.
`explore` is out of scope (separate spec).

---

## Problem

The current `investigate` is a Python-scripted fixed sequence: distance check → pose/move → capture. It gives the LLM no agency — it just runs the script and returns a blob. The result is shallow and the behaviour can't be tuned without touching Python.

## Design

Habits become **workflow documents** — markdown files in `workflows/` that teach the LLM a technique. When the LLM calls a habit tool, the tool spins up a focused sub-agent that reads the workflow doc as its system prompt, uses primitive Pi tools to carry it out, and exits by calling `conclude()`. Main Chotu only sees the clean final result.

---

## Architecture

```
Main brain (_process):
  LLM calls investigate(object_name, location_hint)
    → _do_investigate() loads workflows/investigate.md
    → WorkflowAgent(workflow_doc, params, pi).run()
        own LLMClient, own message history
        system_prompt = workflow_doc + params injected as first user message
        tools = Pi primitives + conclude()
        loops until conclude() called
    → returns conclude payload as standard tool envelope
  Main brain sees: one clean investigate result in memory
```

`tool_chain_active` is already set while `_process` runs, so heartbeats are suppressed for the full sub-agent duration. Human input queues and is processed after `_process` completes (option a — sub-agent runs uninterrupted).

---

## Components

### `core/workflow_agent.py` (new)

```python
class WorkflowAgent:
    def __init__(self, workflow_doc: str, params: dict, pi: PiClient): ...
    async def run(self) -> dict:  # returns conclude payload or error envelope
```

- Own `LLMClient` instance (same local llama-server, sequential — only one LLM call at a time)
- Own `messages: list[dict]` — never touches main brain's `memory`
- System prompt: the workflow doc
- First user message: serialised `params` (e.g. `object_name`, `location_hint`)
- Tools: subset of Pi primitives (move, pose, get_distance, get_battery, capture_vision, get_perception, set_face, speak, wait) + `conclude`
- No `investigate`, `explore`, or `sweep` available inside the sub-agent (no recursion)
- Loop: identical tool-dispatch pattern to main brain's `_process`
- Exit: `conclude` tool call → return payload. No max iteration cap — the workflow doc and LLM decide when to stop.
- On unhandled exception: return `{"ok": false, "error": "workflow_agent: <exc>", ...}`

### `conclude` tool (sub-agent only)

Schema:
```json
{
  "name": "conclude",
  "description": "Call when you have learned enough. Exits the investigation and returns your findings to Chotu.",
  "parameters": {
    "result": { "type": "object", "description": "Your findings. Free-form dict." },
    "status": { "type": "string", "enum": ["done", "failed", "inconclusive"] }
  }
}
```

- Not registered in main `TOOL_SCHEMAS` — only injected into the sub-agent's dispatch map
- `status: failed` or `inconclusive` → envelope has `ok: false` so main Chotu can decide whether to retry

### `workflows/investigate.md` (new, repo root)

```markdown
# Investigate workflow

You are investigating one object. Your goal: learn as much as you can
about it, then call conclude() with your findings.

You have been given an object_name and a location_hint describing roughly
where the object is relative to your current position.

## Steps (use your judgment on each)

1. **Orient** — use location_hint to face the right direction.
   Turn left or right until the object is roughly ahead of you.

2. **Approach** — move forward in small steps, checking distance.
   Stop when close enough to see detail.

3. **Look** — capture_vision. Describe what you see in your monologue.

4. **If too dark** — speak("could you turn on the light?") and wait(10).
   Try capture_vision again. If still dark, conclude as inconclusive.

5. **Examine** — use get_perception to check for color, faces, humans.
   Move to a different angle if the first view was unclear.

6. **Conclude** — when you feel you have enough information, call:
   conclude(
     result={
       "object": "<object_name>",
       "description": "<what you saw>",
       "notable": "<anything surprising or interesting>",
       "location_confirmed": true or false
     },
     status="done"
   )

Stay focused. Do not chase other objects. Do not speak unless asking for
help. When in doubt, look from another angle before concluding.
```

### `core/habits.py` — `investigate` updated

Replaces the current scripted body:

```python
async def investigate(pi: PiClient, object_name: str = "", location_hint: str = "") -> dict:
    from core.workflow_agent import WorkflowAgent
    workflow_doc = (REPO_ROOT / "workflows" / "investigate.md").read_text(encoding="utf-8")
    params = {"object_name": object_name, "location_hint": location_hint}
    agent = WorkflowAgent(workflow_doc, params, pi)
    return await agent.run()
```

`REPO_ROOT` resolves from `Path(__file__).resolve().parent.parent`.

### `core/tools.py` — `investigate` schema updated

New params: `object_name` (string, what to investigate) and `location_hint` (string, rough location from explore result — optional, defaults to empty string).

```json
{
  "name": "investigate",
  "description": "Investigate one specific object up close. Pass the object name and a location hint (e.g. '80cm ahead and left'). Chotu will approach it, examine it from multiple angles, and return detailed findings.",
  "parameters": {
    "object_name": { "type": "string", "description": "Name or description of the object to investigate." },
    "location_hint": { "type": "string", "description": "Rough location from explore result. Leave empty if unknown." }
  },
  "required": ["object_name"]
}
```

---

## Data flow

```
[heartbeat / user input]
  → LLM decides to call investigate("laptop", "80cm ahead, slightly left")
  → _do_investigate(pi, object_name="laptop", location_hint="80cm ahead, slightly left")
  → WorkflowAgent.run():
      user msg: "object_name: laptop\nlocation_hint: 80cm ahead, slightly left"
      LLM turn 1: monologue + move(forward, 2)
      LLM turn 2: capture_vision
      LLM turn 3: get_perception
      LLM turn 4: conclude({...}, status="done")
      → returns envelope
  → main brain memory: [... investigate → {description: "silver laptop, lid open", ...}]
```

---

## Error handling

| Scenario | Behaviour |
|---|---|
| `conclude(status="failed")` | `ok: false` envelope; main LLM decides whether to retry |
| `conclude(status="inconclusive")` | `ok: false` with partial result; LLM can note it and move on |
| Unhandled exception in sub-agent | `ok: false, error: "workflow_agent: <exc>"` returned |
| Pi offline during sub-agent | Each Pi call returns error envelope; LLM can conclude early |
| LLM never calls conclude | Sub-agent has no hard cap — runs until conclude is called. Add a safety cap of 30 iterations in WorkflowAgent to prevent runaway loops (not advertised in workflow doc). |

---

## Testing

- `tests/test_workflow_agent.py` (new) — unit tests with mock LLMClient and mock Pi:
  - Sub-agent calls conclude → returns result
  - Sub-agent calls conclude(status="failed") → ok: false
  - conclude not available in main TOOL_SCHEMAS
- `tests/test_habits_new.py` — replace mock-script tests with: WorkflowAgent called with correct workflow_doc and params
- No on-Pi test required for this task — on-Pi behaviour is user-verified

---

## Files summary

| Path | Action |
|---|---|
| `core/workflow_agent.py` | Create |
| `workflows/investigate.md` | Create (new `workflows/` folder at repo root) |
| `core/habits.py` | Modify — replace `investigate` body |
| `core/tools.py` | Modify — update `investigate` schema |
| `tests/test_workflow_agent.py` | Create |
| `tests/test_habits_new.py` | Modify — update investigate tests |

---

## Out of scope

- `explore` habit-tool (separate spec)
- Mood system (separate spec)
- Spatial mapping / coordinate system
- Sub-agent interruption by user input (deferred)
