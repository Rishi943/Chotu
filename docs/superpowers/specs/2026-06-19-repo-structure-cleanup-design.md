# Repo structure cleanup + repo map — design

**Date:** 2026-06-19
**Status:** approved, ready for implementation plan
**Scope:** surgical cleanup + light `core/` grouping + dead-code/comment pass. No
full restructure. No new runtime dependencies. No cloud calls.

## Goal

Kill file sprawl, give run-outputs one home, remove dead code and off-spec
comments, and add a maintained index so an agent orients by reading one file
instead of grepping the tree. Net effect: fewer re-reads, fewer tokens, less
churn — at a scale (~3,900 LOC / ~40 Python files) where a knowledge-graph MCP
would be pure overhead. Explicitly rejected: AST knowledge-graph tooling
(pays off at ~10× this size).

## Hard constraints (verified, do not break)

- `core/prompts.py` reads `PALIV.md`, `CHOTU_BASE.md`, `CHOTU_REEL.md` from repo
  root. **These three never move.**
- `scripts/gen_builtin_animations.py` writes to `assets/Animations/builtin` by a
  hardcoded path. **Keep the `Animations` directory name exactly** (capital A).
- `core/tools.py` dispatches tools **by string name** (`build_dispatch`); tool
  functions are referenced via schemas/JSON, not always by direct call. Any
  dead-code pass must grep names **as strings** before removing.
- `docs/` is gitignored; only `docs/superpowers/specs|plans` are force-added.
  New `docs/REPO_MAP.md` needs `git add -f`.

## Regression-safety net (the spine of this work)

Every phase ends green against a **pinned baseline**. The rule: *no test that
passes today is allowed to fail.*

1. **Baseline pinned at 176 passed / 3 skipped** (the 2 previously-failing
   `test_animation_studio` tests were stale fakes missing a `timeout=` kwarg;
   fixed pre-spec, production code was correct).
2. **Coverage map** via `coverage` (`pytest --cov=core --cov=scripts`). Dead-code
   removal in *covered* regions is test-protected; *uncovered* regions are the
   danger zone — add a characterization test first or stay conservative.
3. **Static candidates, never authority:** `vulture` (unused funcs/classes/vars)
   + `ruff` (F401 unused imports, F811 redefinitions, unreachable code) produce a
   candidate list only.
4. **Per-candidate manual confirmation:** grep the whole repo (tests, scripts,
   pi_bridge, JSON) for the name including as a string. Remove only on zero real
   references.
5. **Small commits, `pytest` between each** → trivial bisect/revert.
6. **Comments are behavior-neutral:** clean only in files already being touched;
   remove speculative/redundant per CLAUDE.md, keep load-bearing.

`coverage`/`vulture`/`ruff` are dev-only tools installed into `.venv` (free,
local, no cloud). They are not added to `requirements.txt`.

## Phases

Each phase is independently committable and ends at baseline-green.

### Phase 0 — safety net
- (done) fix 2 stale `test_animation_studio` fakes → green baseline 176/3.
- Install `coverage`, `vulture`, `ruff` into `.venv`.
- Capture coverage map; note uncovered modules for Phase 5 caution.

### Phase 1 — run-outputs → one gitignored `out/`
The "new file every run" pain: `dry_run.py` / `benchmark_models.py` / live
sessions each dump a timestamped `.md` into `Test outputs/`. (pytest does *not*
collect these — `testpaths=tests`, scripts excluded.)
- New `out/` replaces `Test outputs/`. Edit 4 write-sites:
  `core/session_profiler.py`, `core/brain.py:~548`, `scripts/dry_run.py:51`,
  `scripts/benchmark_models.py:43`.
- Move existing `Test outputs/*` → `out/`. Swap `.gitignore` entry
  `Test outputs/` → `out/`.
- `/tmp/chotu_*.json` scratch (`chotu_repl`, `chotu_tool`) unchanged.
- Verify: grep shows zero remaining `Test outputs` references; pytest green.

### Phase 2 — junk + second venv
Delete (confirmed): `models/Unconfirmed 68966.crdownload`,
`Github repos/paste-bc9c8d6120de04f9.txt`, empty/dup `assets/Chotu faces/` +
`assets/Chotu_faces/`. **Keep all `.jpeg`s and screenshots.**
- `rm -rf .venv_test/`; broaden `.gitignore` `.venv/` → `.venv*/`.

### Phase 3 — `scripts/` regroup + rename fake-tests
Flat 23 → grouped:
```
scripts/animation/  render_animation, gen_builtin_animations, validate_animation,
                    animation_studio, kinematics_ref, studio.html, studio_3d_prototype.html
scripts/robot/      chotu_tool, chotu_repl, cc_viewer, sim_loop, dry_run
scripts/bench/      benchmark_models, measure_fps_budget, measure_image_tokens,
                    probe_cache (←test_cache), explore_dry (←test_explore_dry)
scripts/faces/      generate_faces.py, chotu_faces.html, faces/*.png
scripts/scenarios/, scripts/test_frames/   unchanged
```
- `test_dry.py` → rename dropping `test_` (manual probe, 0 pytest tests).
- Real pytest-style `test_animation_endpoints.py`, `test_gen_builtin.py`: move to
  `tests/` if hardware-free, else keep as renamed integration probes.
- **Risk:** `tests/test_render_animation.py`, `test_kinematics_ref.py`,
  `test_validate_animation.py` import these scripts → update their import paths.
  Also `scripts/__init__.py` package imports. Gate: pytest green.

### Phase 4 — `core/` light grouping (two clean features only)
```
core/explore/   agent.py (←explore_agent), tools.py (←explore_tools), scope.py, __init__.py
core/chat/      chat.py, prompt.py (←chat_prompt), __init__.py
```
- Update ~8 importers: `core/habits.py`, `core/explore_*`, `core/scope.py`,
  `tests/test_explore_agent|scope|explore_integration|world|explore_tools`,
  `scripts/.../chotu_repl`, `scripts/.../explore_dry`.
- **Everything else in `core/` stays put** — `brain/tools/pi_client/spells/
  motion_lock/loop_helpers/scratchpad/session_profiler/prompts/llm_client/world/
  habits/voice/gui_server/launcher` are the tightly-coupled loop, imported
  everywhere; moving them is high-risk, low-payoff, violates "surgical."
- Verify: `python -c "from core.prompts import SYSTEM_PROMPT"` composes; pytest green.

### Phase 5 — dead-code + comment cleanup (the risky one)
Only now, on top of the net. Per the regression-safety method above:
- Run `vulture`/`ruff` → candidate list.
- Confirm each candidate (string-grep whole repo, mind the dispatch map).
- Remove in small commits, pytest between each.
- Clean off-spec comments only in touched files.

### Phase 6 — repo map
- `docs/REPO_MAP.md`: one line per file, `path → purpose`. `git add -f`.
- `CLAUDE.md` code-layout table gains a pointer to `docs/REPO_MAP.md`.

## Out of scope

- Moving core loop modules; renaming `Animations`; touching `pi_bridge/chotu/`;
  Python 3.14-vs-3.12 venv question (noted, not acted); knowledge-graph tooling;
  any cloud-model call.

## Success criteria

- `pytest` = 176+ passed / 3 skipped at the end of every phase.
- `grep -rn "Test outputs"` and old import paths → zero hits.
- `from core.prompts import SYSTEM_PROMPT` still composes.
- `docs/REPO_MAP.md` exists and matches the tree; `CLAUDE.md` points to it.
- `scripts/` has no `test_*.py` that isn't a real test; flat root is grouped.
