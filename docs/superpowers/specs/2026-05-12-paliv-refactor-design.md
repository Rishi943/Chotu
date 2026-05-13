# PALIV v1 Refactor — Design

**Date:** 2026-05-12
**Scope:** Session 1 of the PALIV pivot. Structural refactor only — no new state machine, no picker, no animations implementation.
**Branch:** `refactor/paliv` → squash-merge into `main`.

## Goal

Reshape the repo from a "Chotu single-instance" codebase into a "PALIV framework hosting a Chotu instance" shape. Land a clean substrate so the next session can drop `picker.py`, `animations.py`, the unified live loop, and habit prompt files into well-defined slots without fighting layout decisions.

Non-goals (deferred to later sessions):
- `picker.py` (state + animation picker LLM call)
- `core/animations.py` (hardcoded IDLE animations)
- `habits/<name>/HABIT.md` prompt content (PLAY skills)
- `brain.py` state-machine rewrite (IDLE/PLAY/LISTEN)
- Mood engine, GUI changes, ReSpeaker, etc.

## Final layout

```
/home/rishi/Rishi/AI/Paliv/
├── CLAUDE.md              ← rewritten as pointer doc (~40 lines)
├── PALIV.md               ← NEW: framework contract
├── CHOTU.md               ← NEW: Chotu persona only
├── PALIV_CC_CONTEXT.md    ← kept (pivot reference, drop later)
├── README.md              ← updated branding/paths
├── .env / .env.example    ← PALIV_* prefixes, Paliv paths
├── core/                  ← Python package (was chotu/)
│   ├── brain.py           ← CHOTU_MODE removed; single live loop entry
│   ├── pi_client.py
│   ├── tools.py
│   ├── voice.py
│   ├── spells.py
│   └── voices/            ← piper model relocated here
├── habits/                ← empty dir + README placeholder
├── pi_bridge/server.py    ← untouched
├── assets/, models/, sounds/, scripts/, tests/, docs/
└── (system_prompt.py deleted)
```

`core/animations.py` is **not** created this session. It will be added when the picker lands; an empty file would be noise.

`habits/` is scaffolded with a placeholder README only because the picker output format (`{state, pick}`) needs the slot decided now.

## Decisions locked

| Question | Decision |
|---|---|
| Repo dir | Move `/home/rishi/Rishi/AI/Chotu` → `/home/rishi/Rishi/AI/Paliv`, fix all path references |
| Python package import name | `core` (`from core.brain import ...`) |
| Old `CHOTU.md` (project spec) | Already deleted by user before this session |
| Doc split | `PALIV.md` = framework contract; new `CHOTU.md` = persona only |
| Animations dir | `core/animations.py` single-file when added; `habits/` empty dir scaffolded now |
| Env var prefix | `CHOTU_*` framework vars → `PALIV_*`; instance vars (`HA_*`, `TUYA_*`, `SPELL_*`, `SPELLS_ENABLED`, `LOCALIS_PIPER_MODEL`, `PI_HOST`) stay |
| Memory dir | `cp -r` old → new path; leave old as backup |
| Branch strategy | `refactor/paliv` branch, squash-merge to `main` |
| CHOTU_MODE removal | Yes, this session (workstream 6, option a) |
| Picker output format (future) | Structured JSON `{state, pick}` |
| Vocabulary | "animations" = IDLE (both background and picked, hardcoded); "habits" = PLAY (long stateful loops) |

## Workstreams

Executed in order on branch `refactor/paliv`.

### 1. Pre-flight
- Verify `git status` and commit the existing unstaged `M chotu/brain.py` and `M pi_bridge/server.py` to `main` under their original intent, so the refactor diff is purely structural.
- Create branch `refactor/paliv`.

### 2. Filesystem rename
- `git mv chotu core` (preserves history). The piper voice dir comes along automatically as `core/voices/`.
- After all git operations in this workstream and §3 are complete, rename the working tree directory itself: `mv /home/rishi/Rishi/AI/Chotu /home/rishi/Rishi/AI/Paliv`. Done last so git operations don't get confused mid-flight.
- Copy Claude Code memory dir: `cp -r ~/.claude/projects/-home-rishi-Rishi-AI-Chotu/memory ~/.claude/projects/-home-rishi-Rishi-AI-Paliv/memory`. Old location stays as backup.

### 3. Package rename (`chotu` → `core`)
Bulk update across the repo:
- All `from chotu.X import Y` → `from core.X import Y`.
- All `import chotu.X` → `import core.X`.
- All `python3 -m chotu.brain` invocations in `scripts/`, `Launch`, `README.md`.
- Touch points known so far: `core/brain.py`, `core/tools.py`, `core/spells.py`, `scripts/dry_run.py`, `tests/`. Verify with `grep -rn "chotu" core/ scripts/ tests/ pi_bridge/ Launch *.md`.

### 4. Env var rename (`CHOTU_*` → `PALIV_*`)

**Renamed (framework-level):**
- `CHOTU_BRAIN_URL`, `CHOTU_BRAIN_KEY`, `CHOTU_BRAIN_MODEL`
- `CHOTU_AUDIO`, `CHOTU_VOICE`, `CHOTU_DEBUG`, `CHOTU_MIC_DEVICE`
- `CHOTU_WAKE_WORD_MODEL`, `CHOTU_WAKE_THRESHOLD`, `CHOTU_WHISPER_MODEL`

**Kept as-is (instance / external service):**
- `PI_HOST`
- `HA_BASE_URL`, `HA_TOKEN`, `HA_LIGHT_ENTITY`
- `TUYA_DEVICE_ID`, `TUYA_LOCAL_KEY`, `TUYA_LIGHT_IP`, `TUYA_VERSION`
- `SPELLS_ENABLED`, `SPELL_LUMOS_SOUND`, `SPELL_NOX_SOUND`, `SPELL_AVADA_SOUND`
- `LOCALIS_PIPER_MODEL`

Update `os.getenv("CHOTU_…")` call sites, `.env`, `.env.example`.

### 5. `system_prompt.py` → `PALIV.md` + `CHOTU.md`

**PALIV.md** (framework contract):
- IDLE / PLAY / LISTEN state definitions (lifted from `paliv-v1-flow.html` + `PALIV_CC_CONTEXT.md`).
- Tool budgets: MAX 1 `speak()` per turn, MAX 12 `set_legs()` per turn, MAX 1 `wait()` per turn.
- Tool availability matrix per state (cast_spell/do_trick/HA → IDLE+LISTEN only; goal_complete → PLAY only).
- Hard interrupts (battery ≤15%, stop word, Pi offline 3 chunks).
- Speech-as-content rule (`speak` is not a tool; message.content is what plays).
- llama-server quirks (disable thinking, model-name match, strip None from assistant messages, multimodal ordering).
- Standard Pi response envelope shape.

**CHOTU.md** (persona, becomes the persona slot of the system prompt):
- Voice: sardonic, dignified, dry, occasionally delighted; aware it's a robot.
- 15-word cap per spoken line.
- 40% dark side, 45% humour, 30% curiosity-breaks (gear-shift, drops act).
- Please mechanic, proportional cursing.
- Physical constraints: 12 servos, 15cm body, 15cm obstacle threshold, default pose speed 50 (brown-out cap).
- Tool-use examples from old `system_prompt.py`, edited to remove stage directions.

**Delete `system_prompt.py`** at the end of this workstream.

**Loader change in `core/brain.py`:** the existing `SYSTEM_PROMPT` constant is rebuilt from file reads at module init:
```python
PALIV_MD = (REPO_ROOT / "PALIV.md").read_text()
CHOTU_MD = (REPO_ROOT / "CHOTU.md").read_text()
SYSTEM_PROMPT = PALIV_MD + "\n\n" + CHOTU_MD
```
Continue to use `.replace("{mode_description}", ...)` style substitution if any template tokens survive — never `.format()` (JSON examples contain `{...}`).

### 6. CHOTU_MODE removal

- Delete `goal_runner_task()` entirely.
- Delete the `CHOTU_MODE` env var read and the reactive/goal branching at startup.
- Rename `brain_loop()` to `live_loop()` and make it the single entry point. Today it behaves like the old reactive loop; next session it grows the IDLE/PLAY/LISTEN state machine.
- Delete `GOAL_ONLY_SCHEMAS` and the schema-merging logic. Remove `goal_complete` from `TOOL_SCHEMAS` entirely — it returns when PLAY ships as a PLAY-state-only tool. The picker session will reintroduce per-state tool gating cleanly; exposing it harmlessly now would just be a hack to undo later.
- Update `MAX_TOOL_ITERATIONS` and any goal-mode-only constants accordingly.

After this workstream, the brain still runs end-to-end against llama-server in the equivalent of "reactive mode," but with no mode switch. Verified via `dry_run.py`.

### 7. CLAUDE.md rewrite

A ~40-line pointer doc:
- One-line project overview.
- "Authoritative docs" section pointing at `PALIV.md`, `CHOTU.md`, `PALIV_CC_CONTEXT.md`.
- "Dev setup" — minimal commands (start llama-server, start brain, start Pi bridge, dry_run).
- "Hard rules" — no new frameworks without asking; cloud LLMs are fallback only; no SQLite yet; don't train "hey chotu" wake word yet; don't design around ReSpeaker.
- "Known LLM quirks" — kept as one-paragraph pointers (model-name match, thinking disabled, assistant-message stripping, multimodal ordering, str.replace not .format).

### 8. Cleanup
- `rm .zip` (98MB orphan at root).
- `rm CHOTU_AUTO.md`.
- Add `sesame-robot-main/` to `.gitignore` (keep on disk as vendor reference).
- Sanity-check that `.gitignore` already covers `.venv/`, `.worktrees/`, `__pycache__/`, `sounds/`, `tests/`.

### 9. Verification before squash-merge
- `python -m core.brain --help` (or equivalent dry start) runs without import errors.
- `python -m scripts.dry_run "walk forward"` round-trips against llama-server end-to-end.
- `grep -rn "chotu\|CHOTU_" core/ scripts/ pi_bridge/ Launch *.md .env .env.example` returns only intentional references (the word "Chotu" as a persona name in `CHOTU.md` is expected; `LOCALIS_PIPER_MODEL` path still contains the persona name as a filesystem fact, also expected).
- Squash-merge `refactor/paliv` → `main`.

## Risks and edge cases

- **Piper voice path:** `LOCALIS_PIPER_MODEL` currently points at `/home/rishi/Rishi/AI/Chotu/chotu/voices/...`. After §2 + §3 it becomes `/home/rishi/Rishi/AI/Paliv/core/voices/...`. Update in `.env` and `.env.example`.
- **Spell soundbite path:** `SPELL_AVADA_SOUND` has an absolute path under `/Chotu/assets/spells/`. Update to `/Paliv/...`.
- **Launch script:** may contain hardcoded paths — read it during §3 and update.
- **Pi-side code (`pi_bridge/server.py`):** runs on the Pi, has no `chotu` Python imports. Not affected by the rename. Untouched this session.
- **Old memory dir:** copied, not moved. If a future session writes to the old path by accident, we lose nothing because the new path was seeded with the latest state. Acceptable.
- **Goal-mode-only tools (`goal_complete`):** removed from `TOOL_SCHEMAS` cleanly in §6. Will return as a PLAY-state-only tool when the state machine lands.

## Exit criteria

The refactor is done when:
1. Working tree is at `/home/rishi/Rishi/AI/Paliv/`.
2. `from core.brain import ...` works; no module is reachable as `chotu.*`.
3. `system_prompt.py` no longer exists; `PALIV.md` and `CHOTU.md` exist and are loaded by `core/brain.py`.
4. No `CHOTU_MODE`, no `goal_runner_task`, no mode-switch branching in `brain.py`.
5. `.env` uses `PALIV_*` for framework vars; `dry_run.py` passes a smoke prompt.
6. `CLAUDE.md` is a short pointer doc.
7. Branch is squash-merged to `main` with a single commit titled e.g. `refactor: rename Chotu codebase to PALIV framework`.
