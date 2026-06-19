# Repo Structure Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove file sprawl, route all run-outputs to one gitignored `out/`, lightly group `core/` by feature, strip dead code and off-spec comments, and add a maintained repo map — without regressing any test.

**Architecture:** Behavior-preserving refactor in independently-committable phases. Each phase ends with the full test suite green against a pinned baseline (176 passed / 3 skipped). Moves are mechanical (paths + imports); the only judgement-heavy phase (dead-code) is gated by static tools → manual string-grep confirmation → per-commit test runs.

**Tech Stack:** Python 3.x, pytest (`testpaths=tests`, `asyncio_mode=auto`), httpx, FastAPI. Dev-only tools added to `.venv`: `coverage`, `vulture`, `ruff`.

## Global Constraints

- **Baseline gate:** `python -m pytest -q` must report **176 passed / 3 skipped** (or more passing, never fewer) at the end of every task. No test that passes today may fail.
- **Never move:** `PALIV.md`, `CHOTU_BASE.md`, `CHOTU_REEL.md` (read by `core/prompts.py` from repo root).
- **Never rename:** the `assets/Animations` directory (hardcoded in `scripts/gen_builtin_animations.py`); the `pi_bridge/chotu/` package.
- **Dead code:** `core/tools.py` dispatches by string name — grep candidate names **as strings** across the whole repo before removing anything.
- **No new runtime deps** in `requirements.txt`; dev tools live in `.venv` only. **No cloud-model calls** of any kind.
- **Commits:** end the message body with `Claude-Session: https://claude.ai/code/session_01X21jrWVJEvWsrCDLzz7pZH`. Do **not** add a `Co-Authored-By` trailer (user preference).
- **Branch:** all work on `chore/repo-cleanup` (already created; spec + test fix already committed at 986da9a).
- `docs/` is gitignored — new files under it (`docs/REPO_MAP.md`) need `git add -f`.
- Run all commands from repo root with `.venv` active (`. .venv/bin/activate`).

---

### Task 1: Phase 0 — dev tooling + coverage baseline

**Files:**
- Create: `docs/superpowers/notes/coverage-baseline-2026-06-19.txt` (reference artifact; `git add -f`)

**Interfaces:**
- Produces: a recorded coverage map identifying uncovered modules, consumed by Task 6's dead-code caution rules.

- [ ] **Step 1: Install dev tooling into `.venv`**

Run: `pip install coverage vulture ruff`
Expected: all three install without touching `requirements.txt`.

- [ ] **Step 2: Confirm green baseline**

Run: `python -m pytest -q`
Expected: `176 passed, 3 skipped`.

- [ ] **Step 3: Capture coverage map**

Run: `coverage run -m pytest -q && coverage report --include="core/*,scripts/*" > docs/superpowers/notes/coverage-baseline-2026-06-19.txt; coverage report --include="core/*,scripts/*" | tail -40`
Expected: a per-file coverage table. Note any file under ~40% — these are Phase 5 danger zones.

- [ ] **Step 4: Commit**

```bash
git add -f docs/superpowers/notes/coverage-baseline-2026-06-19.txt
git commit -m "chore(repo): record coverage baseline for cleanup

Claude-Session: https://claude.ai/code/session_01X21jrWVJEvWsrCDLzz7pZH"
```

---

### Task 2: Phase 1 — run-outputs → gitignored `out/`

**Files:**
- Modify: `core/session_profiler.py` (output dir default)
- Modify: `core/brain.py` (the `"Test outputs"` path it passes to the profiler)
- Modify: `scripts/dry_run.py:51` (`TESTOUT_DIR`)
- Modify: `scripts/benchmark_models.py:43` (`OUTPUT_DIR`)
- Modify: `.gitignore` (`Test outputs/` → `out/`)

**Interfaces:**
- Produces: all run artifacts land in `<repo>/out/`; zero references to `Test outputs` remain.

- [ ] **Step 1: Locate every output-path reference**

Run: `grep -rn "Test outputs" core scripts .gitignore`
Expected: hits in `core/brain.py`, `scripts/dry_run.py:51`, `scripts/benchmark_models.py:43`, `.gitignore` (and a docstring in `benchmark_models.py`).

- [ ] **Step 2: Repoint each path to `out/`**

In `scripts/dry_run.py:51` change `TESTOUT_DIR = Path(__file__).parent.parent / "Test outputs"` → `/ "out"`.
In `scripts/benchmark_models.py:43` change `OUTPUT_DIR = REPO / "Test outputs"` → `/ "out"` (and the docstring mention on line ~6).
In `core/brain.py` change the `... / "Test outputs"` argument to `... / "out"`.
In `core/session_profiler.py` if it has its own default dir, point it at `repo_root / "out"`; otherwise it already receives the dir from `brain.py` — leave it.

- [ ] **Step 3: Move existing artifacts and swap gitignore**

```bash
mkdir -p out && git mv -k "Test outputs"/* out/ 2>/dev/null; mv "Test outputs"/* out/ 2>/dev/null; rmdir "Test outputs" 2>/dev/null
sed -i 's#^Test outputs/#out/#' .gitignore
```
Expected: `out/` holds the former `Test outputs` files; `.gitignore` lists `out/`.

- [ ] **Step 4: Verify no dangling references + suite green**

Run: `grep -rn "Test outputs" core scripts .gitignore; python -m pytest -q`
Expected: zero grep hits; `176 passed, 3 skipped`.

- [ ] **Step 5: Commit**

```bash
git add core/brain.py core/session_profiler.py scripts/dry_run.py scripts/benchmark_models.py .gitignore
git commit -m "chore(repo): route run-outputs to gitignored out/

Claude-Session: https://claude.ai/code/session_01X21jrWVJEvWsrCDLzz7pZH"
```

---

### Task 3: Phase 2 — junk + second venv

**Files:**
- Delete: `models/Unconfirmed 68966.crdownload`, `Github repos/paste-bc9c8d6120de04f9.txt`, `assets/Chotu faces/`, `assets/Chotu_faces/`, `.venv_test/`
- Modify: `.gitignore` (`.venv/` → `.venv*/`)

**Interfaces:**
- Produces: nothing consumed downstream; pure deletion. **Keeps all `.jpeg`s and screenshots.**

- [ ] **Step 1: Confirm nothing references the targets**

Run: `grep -rn "venv_test\|Unconfirmed\|Chotu faces\|Chotu_faces" --include=*.py --include=*.sh --include=*.md . | grep -v docs/superpowers`
Expected: no functional references (spec/plan mentions are fine).

- [ ] **Step 2: Delete junk (all gitignored / untracked — plain rm)**

```bash
rm -f "models/Unconfirmed 68966.crdownload" "Github repos/paste-bc9c8d6120de04f9.txt"
rm -rf "assets/Chotu faces" "assets/Chotu_faces" .venv_test
```
Expected: paths gone; `ls assets | grep -i jpeg` still shows the kept photos.

- [ ] **Step 3: Broaden venv ignore**

In `.gitignore` change `.venv/` → `.venv*/`.

- [ ] **Step 4: Verify suite green (deletes were untracked, so this only proves no breakage)**

Run: `python -m pytest -q`
Expected: `176 passed, 3 skipped`.

- [ ] **Step 5: Commit**

```bash
git add .gitignore
git commit -m "chore(repo): drop dead half-download, stray paste, dup face dirs, second venv

Claude-Session: https://claude.ai/code/session_01X21jrWVJEvWsrCDLzz7pZH"
```

---

### Task 4: Phase 3 — `scripts/` regroup + rename fake-tests

**Files:**
- Move into `scripts/animation/`: `render_animation.py`, `gen_builtin_animations.py`, `validate_animation.py`, `animation_studio.py`, `kinematics_ref.py`, `studio.html`, `studio_3d_prototype.html`
- Move into `scripts/robot/`: `chotu_tool.py`, `chotu_repl.py`, `cc_viewer.py`, `sim_loop.py`, `dry_run.py`
- Move into `scripts/bench/`: `benchmark_models.py`, `measure_fps_budget.py`, `measure_image_tokens.py`, `test_cache.py`→`probe_cache.py`, `test_explore_dry.py`→`explore_dry.py`, `test_dry.py`→`dry_probe.py`
- Move into `scripts/faces/`: `generate_faces.py`, `chotu_faces.html` (and existing `faces/` PNGs already there)
- Decide: `scripts/test_animation_endpoints.py`, `scripts/test_gen_builtin.py` → `tests/` if hardware-free else `scripts/bench/` renamed
- Modify: `tests/test_render_animation.py`, `tests/test_kinematics_ref.py`, `tests/test_validate_animation.py`, `tests/test_animation_studio.py` (import paths)
- Modify: any `scripts/__init__.py` re-exports; add `__init__.py` to each new subpackage if scripts is imported as a package

**Interfaces:**
- Consumes: nothing new.
- Produces: importable module paths change from `scripts.<name>` to `scripts.<group>.<name>`. Test imports updated to match.

- [ ] **Step 1: Find every cross-reference to the scripts being moved**

Run: `grep -rn "from scripts\.\|import scripts\.\|scripts/" tests core | grep -v __pycache__`
Expected: hits in `tests/test_render_animation.py`, `tests/test_kinematics_ref.py`, `tests/test_validate_animation.py`, `tests/test_animation_studio.py` (imports `scripts.animation_studio`).

- [ ] **Step 2: Create subpackages and move files with git**

```bash
cd scripts
for d in animation robot bench faces; do mkdir -p $d && touch $d/__init__.py; done
git mv render_animation.py gen_builtin_animations.py validate_animation.py animation_studio.py kinematics_ref.py studio.html studio_3d_prototype.html animation/
git mv chotu_tool.py chotu_repl.py cc_viewer.py sim_loop.py dry_run.py robot/
git mv benchmark_models.py measure_fps_budget.py measure_image_tokens.py bench/
git mv test_cache.py bench/probe_cache.py
git mv test_explore_dry.py bench/explore_dry.py
git mv test_dry.py bench/dry_probe.py
git mv generate_faces.py chotu_faces.html faces/
cd ..
```
Expected: `scripts/` root no longer flat; `git status` shows renames.

- [ ] **Step 3: Classify the two real test scripts**

Run: `python -m pytest scripts/test_animation_endpoints.py scripts/test_gen_builtin.py -q 2>&1 | tail -15`
Expected: if they pass without a live Pi → `git mv` them into `tests/` (renaming to avoid name clashes); if they need hardware → `git mv` into `scripts/bench/` keeping a non-`test_` name (e.g. `bench/animation_endpoints_probe.py`). Record which path was taken.

- [ ] **Step 4: Fix import paths in tests and any internal script imports**

Update the 4 test files from Step 1: e.g. `from scripts import animation_studio` → `from scripts.animation import animation_studio`; same pattern for `render_animation`, `kinematics_ref`, `validate_animation`. Then:
Run: `grep -rn "from scripts import\|from scripts\." tests scripts | grep -v __pycache__`
Expected: every import names the new subpackage path.

- [ ] **Step 5: Verify suite green**

Run: `python -m pytest -q`
Expected: `176 passed, 3 skipped` (count unchanged unless real tests were moved into `tests/`, in which case it rises — never falls).

- [ ] **Step 6: Commit**

```bash
git add -A scripts tests
git commit -m "chore(scripts): group flat scripts by domain, rename fake-test probes

Claude-Session: https://claude.ai/code/session_01X21jrWVJEvWsrCDLzz7pZH"
```

---

### Task 5: Phase 4 — `core/` explore + chat grouping

**Files:**
- Move: `core/explore_agent.py`→`core/explore/agent.py`, `core/explore_tools.py`→`core/explore/tools.py`, `core/scope.py`→`core/explore/scope.py`; create `core/explore/__init__.py`
- Move: `core/chat.py`→`core/chat/chat.py`, `core/chat_prompt.py`→`core/chat/prompt.py`; create `core/chat/__init__.py`
- Modify importers: `core/habits.py`, `core/explore/agent.py`, `core/explore/tools.py`, `core/explore/scope.py`, `tests/test_explore_agent.py`, `tests/test_scope.py`, `tests/test_explore_integration.py`, `tests/test_world.py`, `tests/test_explore_tools.py`, `scripts/robot/chotu_repl.py`, `scripts/bench/explore_dry.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: new import paths — `core.explore.agent`, `core.explore.tools`, `core.explore.scope`, `core.chat.chat`, `core.chat.prompt`. The `__init__.py` files may re-export top names (e.g. `from .agent import ExploreAgent`) to soften call sites; decide per actual usage.

- [ ] **Step 1: List every importer of the modules being moved**

Run: `grep -rn "explore_agent\|explore_tools\|core\.scope\|core import scope\|from core\.chat\|core\.chat_prompt\|from core import chat\b" core scripts tests | grep -v __pycache__`
Expected: the importer set above.

- [ ] **Step 2: Create subpackages and move with git**

```bash
mkdir -p core/explore core/chat && touch core/explore/__init__.py core/chat/__init__.py
git mv core/explore_agent.py core/explore/agent.py
git mv core/explore_tools.py core/explore/tools.py
git mv core/scope.py core/explore/scope.py
git mv core/chat.py core/chat/chat.py
git mv core/chat_prompt.py core/chat/prompt.py
```

- [ ] **Step 3: Rewrite imports at every call site**

Apply these substitutions across the importer set (and inside the moved files themselves):
`from core.explore_agent import` → `from core.explore.agent import`;
`from core.explore_tools import` → `from core.explore.tools import`;
`from core.scope import` → `from core.explore.scope import`;
`from core import world` stays (world unchanged);
`from core.chat_prompt import` → `from core.chat.prompt import`;
`from core.chat import` → `from core.chat.chat import` (or add a re-export in `core/chat/__init__.py`: `from .chat import *` and keep `from core.chat import`).
Then:
Run: `grep -rn "explore_agent\|explore_tools\|core\.scope\|chat_prompt" core scripts tests | grep -v __pycache__`
Expected: zero hits on the old paths.

- [ ] **Step 4: Verify prompt composition + suite green**

Run: `python -c "from core.prompts import SYSTEM_PROMPT; print(len(SYSTEM_PROMPT))" && python -m pytest -q`
Expected: prints a non-zero length; `176 passed, 3 skipped`.

- [ ] **Step 5: Commit**

```bash
git add -A core scripts tests
git commit -m "refactor(core): group explore and chat features into subpackages

Claude-Session: https://claude.ai/code/session_01X21jrWVJEvWsrCDLzz7pZH"
```

---

### Task 6: Phase 5 — dead-code + off-spec comment cleanup

**Files:**
- Modify: whichever `core/` and `scripts/` files static analysis flags and manual confirmation clears.

**Interfaces:**
- Consumes: the coverage map from Task 1 (uncovered files get extra caution).
- Produces: nothing structural — symbols removed, comments trimmed; public/dispatch surface unchanged.

- [ ] **Step 1: Generate the candidate list**

Run: `vulture core scripts --min-confidence 80 | tee /tmp/vulture.txt; ruff check core scripts --select F401,F811,F841 2>&1 | tail -40`
Expected: a list of unused imports/vars/functions. **This is candidates only.**

- [ ] **Step 2: Confirm each candidate is truly dead**

For every flagged symbol, run: `grep -rn "<symbol_name>" core scripts tests pi_bridge assets --include=*.py --include=*.json | grep -v __pycache__`
Keep the symbol if it appears in a dispatch map, tool schema, JSON, or any test. Remove only on zero real references. Cross-check uncovered files (Task 1 map) — if a candidate lives in an uncovered file, prefer adding a quick characterization test over deleting blind, or leave it and note it.

- [ ] **Step 3: Remove confirmed-dead code in small commits**

Delete one logical group (e.g. unused imports first, then one dead function), then:
Run: `python -m pytest -q`
Expected: `176 passed, 3 skipped` after each removal. Commit between groups:
```bash
git add -A core scripts
git commit -m "refactor: remove confirmed-dead <thing>

Claude-Session: https://claude.ai/code/session_01X21jrWVJEvWsrCDLzz7pZH"
```

- [ ] **Step 4: Trim off-spec comments in touched files only**

In files already modified above, remove speculative/redundant comments per CLAUDE.md (keep load-bearing ones). Do not sweep untouched files.
Run: `python -m pytest -q`
Expected: `176 passed, 3 skipped`.

- [ ] **Step 5: Final commit for comment pass**

```bash
git add -A core scripts
git commit -m "style: trim off-spec comments in touched modules

Claude-Session: https://claude.ai/code/session_01X21jrWVJEvWsrCDLzz7pZH"
```

---

### Task 7: Phase 6 — repo map + CLAUDE.md pointer

**Files:**
- Create: `docs/REPO_MAP.md` (`git add -f`)
- Modify: `CLAUDE.md` (code-layout table gains a pointer line)

**Interfaces:**
- Produces: `docs/REPO_MAP.md` — one line per source file, `path → purpose`, reflecting the post-cleanup tree.

- [ ] **Step 1: Enumerate the final tracked source tree**

Run: `git ls-files 'core/**' 'scripts/**' 'pi_bridge/**' | grep '\.py$'`
Expected: the post-refactor file list to document.

- [ ] **Step 2: Write `docs/REPO_MAP.md`**

One line per file: `` `path` — purpose ``, grouped by top-level dir (core loop, core/explore, core/chat, scripts/animation, scripts/robot, scripts/bench, pi_bridge). Mirror the existing CLAUDE.md code-layout table style; one sentence each, load-bearing facts only.

- [ ] **Step 3: Point CLAUDE.md at the map**

In `CLAUDE.md`'s "Code layout" section, add a line directing readers to `docs/REPO_MAP.md` for the per-file index.

- [ ] **Step 4: Verify suite green + map matches tree**

Run: `python -m pytest -q && comm -23 <(git ls-files 'core/**' 'scripts/**' | grep '\.py$' | sort) <(grep -oE '`[^`]+\.py`' docs/REPO_MAP.md | tr -d '`' | sort)`
Expected: `176 passed, 3 skipped`; the `comm` diff lists any `.py` missing from the map (should be empty or only intentional omissions like `__init__.py`).

- [ ] **Step 5: Commit**

```bash
git add -f docs/REPO_MAP.md
git add CLAUDE.md
git commit -m "docs(repo): add REPO_MAP index and point CLAUDE.md at it

Claude-Session: https://claude.ai/code/session_01X21jrWVJEvWsrCDLzz7pZH"
```

---

## Self-Review

**Spec coverage:** Phase 0→Task 1, Phase 1→Task 2, Phase 2→Task 3, Phase 3→Task 4, Phase 4→Task 5, Phase 5→Task 6, Phase 6→Task 7. Hard constraints (prompt files, Animations name, string dispatch, no cloud, commit trailer) are in Global Constraints and reinforced in the relevant tasks. All spec sections mapped.

**Placeholders:** Task 4 Step 3 and Task 6 Steps 2–3 require in-the-moment judgement (classify-then-route, confirm-then-remove) — these are decision procedures with explicit pass/fail commands, not unfilled placeholders. No "TBD"/"handle edge cases"/"similar to Task N" present.

**Type/path consistency:** New import paths (`core.explore.agent/tools/scope`, `core.chat.chat/prompt`, `scripts.<group>.<name>`) are used identically in the move step and the import-rewrite step of each task. `out/` is the single output dir name throughout.
