# Chotu Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `./launch.sh` that runs an upgraded config screen, starts the services the chosen config needs (local `llama-server`, optional Pi bridge) with health gating, then runs the brain in the foreground — tearing down llama on exit.

**Architecture:** Extend the pure-logic `core/launcher.py` (presets + `LauncherState`) and add a thin, dependency-injected `core/run.py` orchestrator split into a testable pure planner (`plan_services`) plus side-effecting helpers (`spawn_llama`/`spawn_bridge`/`wait_healthy`/`teardown`). `launch.sh` activates the venv and runs `python -m core.run`. The brain is unchanged — `core/run.py` sets env, late-imports `core.brain`, and calls `asyncio.run(core.brain.main())`.

**Tech Stack:** Python 3.x, stdlib `subprocess`/`signal`/`dataclasses`, `httpx` (already a dep) for health polls, pytest.

## Global Constraints

- **Baseline gate:** `python -m pytest -q` reports **176 passed / 3 skipped** (or more), never fewer, at the end of every task. Use `.venv/bin/python`.
- **No new runtime dependency** in `requirements.txt`. No cloud-model calls from the launcher itself (health checks hit only local llama + the Pi).
- **`.env` points `PALIV_BRAIN_URL` at DashScope** → for local-llama presets, `to_env()` overrides `PALIV_BRAIN_URL=http://127.0.0.1:8080/v1` and clears `PALIV_BRAIN_KEY`.
- **Provider note (from CLAUDE.md):** DashScope Qwen uses `PALIV_LLM_PROVIDER=local` (same code path) — so "is this a local llama-server preset" is a distinct `spawn_llama` flag, NOT `provider == "local"`.
- **Commits:** clean conventional-commit messages — **no** `Co-Authored-By` or `Claude-Session` trailers.
- **Branch:** all work on `feat/launcher` (already created; spec committed at `52d9783`).
- `docs/` is gitignored — `docs/REPO_MAP.md` edits need no special handling (already tracked via force-add).

---

### Task 1: Presets (4) + `llama_args` builder

**Files:**
- Modify: `core/launcher.py` (the `PRESETS` list + a new `llama_args` function)
- Test: `tests/test_launcher.py` (replace `test_presets_order_and_content`)

**Interfaces:**
- Produces: `PRESETS: list[dict]` with keys `label, provider, model, tag, spawn_llama, mmproj, extra`; `llama_args(preset: dict, models_dir: Path) -> list[str]`.

- [ ] **Step 1: Replace the presets test**

In `tests/test_launcher.py` replace `test_presets_order_and_content` with:

```python
def test_presets_order_and_content():
    labels = [p["label"] for p in PRESETS]
    assert labels == ["Gemma", "Qwen", "Qwen cloud", "Claude"]
    gemma, qwen, qcloud, claude = PRESETS
    assert gemma["provider"] == "local" and gemma["spawn_llama"] is True
    assert gemma["model"] == "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    assert qwen["spawn_llama"] is True and qwen["model"] == "Qwen3.5-4B-Q4_K_M.gguf"
    assert qcloud["provider"] == "local" and qcloud["spawn_llama"] is False
    assert qcloud["model"] == "qwen3.5-flash"
    assert claude["provider"] == "claude" and claude["spawn_llama"] is False


def test_llama_args_gemma_has_swa_full():
    from pathlib import Path
    args = launcher.llama_args(PRESETS[0], Path("/m"))
    assert args[0] == "llama-server"
    assert "/m/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf" in args
    assert "/m/gemma_mmproj-BF16.gguf" in args
    assert "--swa-full" in args


def test_llama_args_qwen_no_swa_full():
    from pathlib import Path
    args = launcher.llama_args(PRESETS[1], Path("/m"))
    assert "/m/mmproj-BF16.gguf" in args
    assert "--swa-full" not in args
```

- [ ] **Step 2: Run — expect failure**

Run: `.venv/bin/python -m pytest tests/test_launcher.py::test_presets_order_and_content tests/test_launcher.py::test_llama_args_gemma_has_swa_full -q`
Expected: FAIL (4 presets / `llama_args` not defined).

- [ ] **Step 3: Implement presets + builder**

In `core/launcher.py` replace `PRESETS` and add `llama_args`:

```python
from pathlib import Path

PRESETS = [
    {"label": "Gemma", "provider": "local", "model": "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf",
     "tag": "local", "spawn_llama": True, "mmproj": "gemma_mmproj-BF16.gguf",
     "extra": ["--swa-full", "--reasoning-budget", "-1"]},
    {"label": "Qwen", "provider": "local", "model": "Qwen3.5-4B-Q4_K_M.gguf",
     "tag": "local", "spawn_llama": True, "mmproj": "mmproj-BF16.gguf", "extra": []},
    {"label": "Qwen cloud", "provider": "local", "model": "qwen3.5-flash",
     "tag": "cloud — DashScope, spends tokens", "spawn_llama": False, "mmproj": None, "extra": None},
    {"label": "Claude", "provider": "claude", "model": "claude-sonnet-4-6",
     "tag": "cloud — spends tokens", "spawn_llama": False, "mmproj": None, "extra": None},
]


def llama_args(preset: dict, models_dir: Path) -> list[str]:
    """Build the llama-server argv for a local preset. Mirrors the repo's Launch flags."""
    return [
        "llama-server",
        "-m", str(models_dir / preset["model"]),
        "--mmproj", str(models_dir / preset["mmproj"]),
        "--port", "8080", "-ngl", "99", "-c", "16384", "--parallel", "1",
        "--image-max-tokens", "140", "--temp", "0.7", "--top-p", "0.95", "--top-k", "64",
        *preset["extra"],
    ]
```

- [ ] **Step 4: Run — expect pass**

Run: `.venv/bin/python -m pytest tests/test_launcher.py -q`
Expected: the 3 new tests pass; other `test_launcher` tests may now FAIL (preset count / rows) — that's expected and fixed in Task 2. If only the 3 target tests are run they pass.

Run (targeted): `.venv/bin/python -m pytest tests/test_launcher.py::test_presets_order_and_content tests/test_launcher.py::test_llama_args_gemma_has_swa_full tests/test_launcher.py::test_llama_args_qwen_no_swa_full -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add core/launcher.py tests/test_launcher.py
git commit -m "feat(launcher): add Qwen-cloud preset and llama_args builder"
```

---

### Task 2: `LauncherState` — input/stats/pi_mode fields, rows, to_env

**Files:**
- Modify: `core/launcher.py` (`TOGGLES`, row constants, `LauncherState`, `apply_key`, `seed_from_env`, `to_env`, `render`)
- Test: `tests/test_launcher.py` (rewrite the row/seed/to_env/render tests)

**Interfaces:**
- Consumes: `PRESETS` (Task 1).
- Produces: `LauncherState(preset_idx, persona, input_mode, mute, debug, stats, pi_mode, focus)`; row constants `N_ROWS=11, START_ROW=10, PERSONA_ROW=4, INPUT_ROW=5, PIBRIDGE_ROW=9, PRESET_ROWS=(0,1,2,3), TOGGLE_BY_ROW={6:"mute",7:"debug",8:"stats"}`; `to_env() -> dict[str,str]`.

- [ ] **Step 1: Rewrite the affected tests**

In `tests/test_launcher.py` replace the body tests (`test_to_env_*`, `test_seed_*`, focus/select/render tests) with:

```python
def test_to_env_defaults_qwen_local_overrides_url():
    s = LauncherState()  # preset_idx 1 = Qwen (local llama)
    env = s.to_env()
    assert env["PALIV_LLM_PROVIDER"] == "local"
    assert env["PALIV_BRAIN_MODEL"] == "Qwen3.5-4B-Q4_K_M.gguf"
    assert env["PALIV_BRAIN_URL"] == "http://127.0.0.1:8080/v1"
    assert env["PALIV_BRAIN_KEY"] == ""
    assert env["PALIV_VOICE"] == "0" and env["PALIV_PTT"] == "0"
    assert env["PALIV_MUTE"] == "0" and env["PALIV_DEBUG"] == "0"
    assert env["PALIV_SHOW_STATS"] == "0" and env["PALIV_PERSONA"] == ""


def test_to_env_dashscope_leaves_url_unset():
    s = LauncherState(preset_idx=2)  # Qwen cloud
    env = s.to_env()
    assert env["PALIV_BRAIN_MODEL"] == "qwen3.5-flash"
    assert "PALIV_BRAIN_URL" not in env
    assert "PALIV_BRAIN_KEY" not in env


def test_to_env_claude_provider():
    s = LauncherState(preset_idx=3)
    env = s.to_env()
    assert env["PALIV_LLM_PROVIDER"] == "claude"
    assert "PALIV_BRAIN_URL" not in env


def test_to_env_input_modes_map_to_voice_ptt():
    assert LauncherState(input_mode="text").to_env()["PALIV_VOICE"] == "0"
    voice = LauncherState(input_mode="voice").to_env()
    assert voice["PALIV_VOICE"] == "1" and voice["PALIV_PTT"] == "0"
    ptt = LauncherState(input_mode="ptt").to_env()
    assert ptt["PALIV_PTT"] == "1" and ptt["PALIV_VOICE"] == "0"


def test_seed_input_and_stats_and_pi_default():
    s = LauncherState.seed_from_env({"PALIV_PTT": "1", "PALIV_SHOW_STATS": "1"})
    assert s.input_mode == "ptt" and s.stats is True
    assert s.pi_mode == "running"
    assert LauncherState.seed_from_env({"PALIV_VOICE": "1"}).input_mode == "voice"


def test_select_input_row_cycles():
    s = LauncherState(focus=5, input_mode="text")
    _, s = s.apply_key("SELECT"); assert s.input_mode == "voice"
    _, s = s.apply_key("SELECT"); assert s.input_mode == "ptt"
    _, s = s.apply_key("SELECT"); assert s.input_mode == "text"


def test_select_pi_bridge_row_cycles():
    s = LauncherState(focus=9, pi_mode="running")
    _, s = s.apply_key("SELECT"); assert s.pi_mode == "start"
    _, s = s.apply_key("SELECT"); assert s.pi_mode == "offline"
    _, s = s.apply_key("SELECT"); assert s.pi_mode == "running"


def test_select_stats_toggle():
    s = LauncherState(focus=8)
    _, s = s.apply_key("SELECT"); assert s.stats is True


def test_down_wraps_at_n_rows():
    s = LauncherState(focus=10)
    _, s = s.apply_key("DOWN"); assert s.focus == 0
    _, s = LauncherState(focus=0).apply_key("UP"); assert s.focus == 10


def test_select_on_start_returns_start():
    action, _ = LauncherState(focus=10).apply_key("SELECT")
    assert action == "start"


def test_render_shows_new_rows():
    text = LauncherState(preset_idx=0, mute=True, stats=True, persona="reel").render()
    assert "Gemma" in text and "Qwen cloud" in text and "Claude" in text
    assert "Input:" in text and "Pi bridge:" in text
    assert "[✓] Mute" in text and "[✓] Stats" in text
```

Keep `test_seed_mute_flag_checks_mute`, `test_seed_claude_provider_selects_claude` (idx now 3 — update to `== 3`), `test_seed_gemma_model_selects_gemma`, `test_seed_reel_persona`, `test_select_on_preset_row_is_radio`, `test_select_on_toggle_flips_only_that_toggle` (mute is now row 6 — update `focus=6`), `test_select_on_persona_cycles` (persona row now 4 — update `focus=4`), `test_quit_key_returns_quit`, `test_unknown_key_is_noop`, and the two `run_launcher` no-op tests. Update the row indices noted in parentheses.

- [ ] **Step 2: Run — expect failures**

Run: `.venv/bin/python -m pytest tests/test_launcher.py -q`
Expected: many FAIL (new fields/rows not implemented).

- [ ] **Step 3: Implement the state changes**

In `core/launcher.py`:

```python
TOGGLES = {"mute": "PALIV_MUTE", "debug": "PALIV_DEBUG", "stats": "PALIV_SHOW_STATS"}

N_ROWS = 11
START_ROW = 10
PIBRIDGE_ROW = 9
PERSONA_ROW = 4
INPUT_ROW = 5
PRESET_ROWS = (0, 1, 2, 3)
TOGGLE_BY_ROW = {6: "mute", 7: "debug", 8: "stats"}
_INPUT_CYCLE = ["text", "voice", "ptt"]
_PI_CYCLE = ["running", "start", "offline"]
```

Replace the `LauncherState` dataclass fields and methods:

```python
@dataclass
class LauncherState:
    preset_idx: int = 1
    mute: bool = False
    debug: bool = False
    stats: bool = False
    input_mode: str = "text"      # text | voice | ptt
    pi_mode: str = "running"      # running | start | offline
    persona: str = "base"
    focus: int = 0

    @classmethod
    def seed_from_env(cls, env: dict[str, str]) -> "LauncherState":
        provider = env.get("PALIV_LLM_PROVIDER", "local")
        model = env.get("PALIV_BRAIN_MODEL", "")
        if provider == "claude":
            preset_idx = 3
        elif model == "qwen3.5-flash":
            preset_idx = 2
        elif "gemma" in model.lower():
            preset_idx = 0
        else:
            preset_idx = 1
        if env.get("PALIV_PTT") == "1":
            input_mode = "ptt"
        elif env.get("PALIV_VOICE") == "1":
            input_mode = "voice"
        else:
            input_mode = "text"
        return cls(
            preset_idx=preset_idx,
            mute=env.get("PALIV_MUTE") == "1",
            debug=env.get("PALIV_DEBUG") == "1",
            stats=env.get("PALIV_SHOW_STATS") == "1",
            input_mode=input_mode,
            pi_mode="running",
            persona="reel" if env.get("PALIV_PERSONA") == "reel" else "base",
        )

    def apply_key(self, key: str) -> tuple[str, "LauncherState"]:
        if key == "QUIT":
            return ("quit", self)
        if key == "DOWN":
            self.focus = (self.focus + 1) % N_ROWS
            return ("continue", self)
        if key == "UP":
            self.focus = (self.focus - 1) % N_ROWS
            return ("continue", self)
        if key == "SELECT":
            if self.focus == START_ROW:
                return ("start", self)
            if self.focus in PRESET_ROWS:
                self.preset_idx = self.focus
            elif self.focus in TOGGLE_BY_ROW:
                name = TOGGLE_BY_ROW[self.focus]
                setattr(self, name, not getattr(self, name))
            elif self.focus == INPUT_ROW:
                self.input_mode = _INPUT_CYCLE[(_INPUT_CYCLE.index(self.input_mode) + 1) % 3]
            elif self.focus == PIBRIDGE_ROW:
                self.pi_mode = _PI_CYCLE[(_PI_CYCLE.index(self.pi_mode) + 1) % 3]
            elif self.focus == PERSONA_ROW:
                self.persona = "reel" if self.persona == "base" else "base"
            return ("continue", self)
        return ("continue", self)

    def to_env(self) -> dict[str, str]:
        preset = PRESETS[self.preset_idx]
        env = {
            "PALIV_LLM_PROVIDER": preset["provider"],
            "PALIV_BRAIN_MODEL": preset["model"],
            "PALIV_PERSONA": "reel" if self.persona == "reel" else "",
            "PALIV_VOICE": "1" if self.input_mode == "voice" else "0",
            "PALIV_PTT": "1" if self.input_mode == "ptt" else "0",
        }
        for field_name, var in TOGGLES.items():
            env[var] = "1" if getattr(self, field_name) else "0"
        if preset["spawn_llama"]:
            env["PALIV_BRAIN_URL"] = "http://127.0.0.1:8080/v1"
            env["PALIV_BRAIN_KEY"] = ""
        return env

    def render(self) -> str:
        def cur(row): return "›" if self.focus == row else " "
        lines = ["  Chotu brain — launch config        (↑/↓ move · space toggle · enter start · q quit)", "", "  Model:"]
        for i, p in enumerate(PRESETS):
            mark = "•" if self.preset_idx == i else " "
            tag = f"  ({p['tag']})"
            lines.append(f"  {cur(i)} ({mark}) {p['label']}{tag}")
        lines.append("")
        lines.append(f"  {cur(PERSONA_ROW)} Persona: {self.persona} ▸")
        lines.append(f"  {cur(INPUT_ROW)} Input: {self.input_mode} ▸")
        lines.append("")
        for row, name, label in [(6, "mute", "Mute"), (7, "debug", "Debug"), (8, "stats", "Stats")]:
            box = "✓" if getattr(self, name) else " "
            lines.append(f"  {cur(row)} [{box}] {label}")
        lines.append(f"  {cur(PIBRIDGE_ROW)} Pi bridge: {self.pi_mode} ▸")
        lines.append("")
        lines.append(f"  {cur(START_ROW)} Start ▶")
        return "\n".join(lines)
```

- [ ] **Step 4: Run — expect pass**

Run: `.venv/bin/python -m pytest tests/test_launcher.py -q`
Expected: all `test_launcher` tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/launcher.py tests/test_launcher.py
git commit -m "feat(launcher): input-mode/stats/pi-bridge rows + local-URL override in to_env"
```

---

### Task 3: `run_launcher()` returns its state

**Files:**
- Modify: `core/launcher.py` (`run_launcher` signature/returns)
- Test: `tests/test_launcher.py` (extend the two no-op tests + add a return-type assert)

**Interfaces:**
- Produces: `run_launcher() -> LauncherState`. Interactive path applies `to_env()` to `os.environ` and returns the final state. No-op paths (`PALIV_NO_LAUNCHER=1` or non-TTY) return `LauncherState.seed_from_env(dict(os.environ))` **without** mutating `os.environ`.

- [ ] **Step 1: Update the no-op tests to assert the return**

In `tests/test_launcher.py`, append to both `test_run_launcher_noop_*` tests:

```python
    state = launcher.run_launcher()
    assert os.environ["PALIV_BRAIN_MODEL"] == "sentinel-unchanged"
    from core.launcher import LauncherState
    assert isinstance(state, LauncherState)
```

(Replace the existing single `launcher.run_launcher()` call line in each.)

- [ ] **Step 2: Run — expect failure**

Run: `.venv/bin/python -m pytest tests/test_launcher.py -k run_launcher -q`
Expected: FAIL (`run_launcher` returns `None`).

- [ ] **Step 3: Implement**

In `core/launcher.py` `run_launcher`: change the early-return and the end:

```python
    if os.getenv("PALIV_NO_LAUNCHER") == "1" or not sys.stdin.isatty():
        return LauncherState.seed_from_env(dict(os.environ))
```

and at the end (after the env-apply loop), `return state`:

```python
    for var, val in state.to_env().items():
        os.environ[var] = val
    return state
```

- [ ] **Step 4: Run — expect pass**

Run: `.venv/bin/python -m pytest tests/test_launcher.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/launcher.py tests/test_launcher.py
git commit -m "feat(launcher): run_launcher returns the resolved LauncherState"
```

---

### Task 4: `core/run.py` pure planner

**Files:**
- Create: `core/run.py`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: `LauncherState`, `PRESETS`, `llama_args` (Tasks 1–3).
- Produces: `@dataclass ServicePlan(spawn_llama: bool, llama_args: list[str], health_checks: list[tuple[str, str]], start_bridge: bool)`; `plan_services(state: LauncherState, repo_root: Path, pi_host: str) -> ServicePlan`.

- [ ] **Step 1: Write the planner tests**

Create `tests/test_run.py`:

```python
from pathlib import Path
from core.launcher import LauncherState
from core.run import plan_services


def test_plan_local_preset_spawns_llama_and_checks_both():
    plan = plan_services(LauncherState(preset_idx=1), Path("/repo"), "http://pi:7000")
    assert plan.spawn_llama is True
    assert "/repo/models/Qwen3.5-4B-Q4_K_M.gguf" in plan.llama_args
    names = [n for n, _ in plan.health_checks]
    assert names == ["llama", "pi"]
    assert ("llama", "http://127.0.0.1:8080/health") in plan.health_checks


def test_plan_dashscope_no_llama_pi_only():
    plan = plan_services(LauncherState(preset_idx=2), Path("/repo"), "http://pi:7000")
    assert plan.spawn_llama is False and plan.llama_args == []
    assert [n for n, _ in plan.health_checks] == ["pi"]


def test_plan_offline_skips_pi_check():
    plan = plan_services(LauncherState(preset_idx=3, pi_mode="offline"), Path("/repo"), "http://pi:7000")
    assert plan.health_checks == []


def test_plan_pi_start_sets_start_bridge():
    plan = plan_services(LauncherState(preset_idx=1, pi_mode="start"), Path("/repo"), "http://pi:7000")
    assert plan.start_bridge is True
```

- [ ] **Step 2: Run — expect failure**

Run: `.venv/bin/python -m pytest tests/test_run.py -q`
Expected: FAIL (`core.run` does not exist).

- [ ] **Step 3: Implement the planner**

Create `core/run.py`:

```python
"""Orchestrator for `./launch.sh`: config screen -> services -> brain (foreground).

Pure planning (`plan_services`) is separated from side-effecting helpers so the
decision logic is unit-tested. `main()` wires them and hands off to core.brain."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.launcher import PRESETS, LauncherState, llama_args

LLAMA_HEALTH = "http://127.0.0.1:8080/health"


@dataclass
class ServicePlan:
    spawn_llama: bool
    llama_args: list[str]
    health_checks: list[tuple[str, str]]
    start_bridge: bool


def plan_services(state: LauncherState, repo_root: Path, pi_host: str) -> ServicePlan:
    preset = PRESETS[state.preset_idx]
    spawn = bool(preset["spawn_llama"])
    args = llama_args(preset, repo_root / "models") if spawn else []
    checks: list[tuple[str, str]] = []
    if spawn:
        checks.append(("llama", LLAMA_HEALTH))
    if state.pi_mode != "offline":
        checks.append(("pi", f"{pi_host.rstrip('/')}/health"))
    return ServicePlan(spawn_llama=spawn, llama_args=args,
                       health_checks=checks, start_bridge=state.pi_mode == "start")
```

- [ ] **Step 4: Run — expect pass**

Run: `.venv/bin/python -m pytest tests/test_run.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add core/run.py tests/test_run.py
git commit -m "feat(run): pure service planner for the launcher orchestrator"
```

---

### Task 5: `core/run.py` side-effecting helpers

**Files:**
- Modify: `core/run.py` (add `_http_ok`, `wait_healthy`, `spawn_llama`, `spawn_bridge`, `teardown`)
- Test: `tests/test_run.py` (add fakes-based tests)

**Interfaces:**
- Produces: `wait_healthy(checks, probe=_http_ok, timeout=120.0, interval=1.0, sleep=time.sleep) -> None` (raises `TimeoutError`); `spawn_llama(args, log_path) -> subprocess.Popen`; `spawn_bridge(log_path) -> subprocess.Popen`; `teardown(procs) -> None`.

- [ ] **Step 1: Write helper tests with fakes**

Append to `tests/test_run.py`:

```python
import pytest
from core import run as runmod


def test_wait_healthy_returns_when_all_ok():
    seen = {"n": 0}
    def probe(url):
        seen["n"] += 1
        return seen["n"] >= 2          # first poll False, then True
    runmod.wait_healthy([("llama", "u")], probe=probe, timeout=5, interval=0, sleep=lambda s: None)
    assert seen["n"] >= 2


def test_wait_healthy_times_out():
    with pytest.raises(TimeoutError):
        runmod.wait_healthy([("pi", "u")], probe=lambda u: False,
                            timeout=0.0, interval=0, sleep=lambda s: None)


class _FakeProc:
    def __init__(self): self.terminated = self.killed = False; self._alive = True
    def terminate(self): self.terminated = True
    def wait(self, timeout=None):
        if self._alive and self.terminated: self._alive = False; return 0
        raise __import__("subprocess").TimeoutExpired("p", timeout)
    def kill(self): self.killed = True; self._alive = False


def test_teardown_terminates_then_kills_if_needed():
    graceful = _FakeProc()
    runmod.teardown([graceful])
    assert graceful.terminated and not graceful.killed

    stubborn = _FakeProc()
    stubborn.wait = lambda timeout=None: (_ for _ in ()).throw(__import__("subprocess").TimeoutExpired("p", timeout))
    runmod.teardown([stubborn])
    assert stubborn.killed
```

- [ ] **Step 2: Run — expect failure**

Run: `.venv/bin/python -m pytest tests/test_run.py -k "wait_healthy or teardown" -q`
Expected: FAIL (helpers undefined).

- [ ] **Step 3: Implement the helpers**

Add to `core/run.py` (imports at top: `import subprocess, time, httpx`):

```python
def _http_ok(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2.0).status_code == 200
    except Exception:
        return False


def wait_healthy(checks, probe=_http_ok, timeout=120.0, interval=1.0, sleep=time.sleep) -> None:
    """Poll each (name, url) until all return True, or raise TimeoutError after `timeout`."""
    deadline = time.monotonic() + timeout
    pending = list(checks)
    while pending:
        pending = [(n, u) for (n, u) in pending if not probe(u)]
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"unhealthy after {timeout}s: {[n for n, _ in pending]}")
        sleep(interval)


def spawn_llama(args: list[str], log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "w")
    return subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def spawn_bridge(log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "w")
    cmd = ["ssh", "chotu@chotu.local",
           "sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py"]
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def teardown(procs) -> None:
    for p in procs:
        try:
            p.terminate()
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        except Exception:
            pass
```

- [ ] **Step 4: Run — expect pass**

Run: `.venv/bin/python -m pytest tests/test_run.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/run.py tests/test_run.py
git commit -m "feat(run): health-poll + llama/bridge spawn + teardown helpers"
```

---

### Task 6: `main()` wiring + `launch.sh`

**Files:**
- Modify: `core/run.py` (add `main()` + `if __name__ == "__main__"`)
- Create: `launch.sh`

**Interfaces:**
- Consumes: `plan_services`, helpers (Tasks 4–5), `run_launcher` (Task 3), `core.brain.main` (existing, brain.py:474).
- Produces: `main() -> None` (no unit test — it execs the brain; verified manually).

- [ ] **Step 1: Implement `main()`**

Add to `core/run.py` (top imports: `import asyncio, os, sys`; `from dotenv import load_dotenv`; `from core.launcher import run_launcher`):

```python
REPO = Path(__file__).resolve().parent.parent


def main() -> None:
    load_dotenv()                                   # PI_HOST + cloud creds for orchestration
    pi_host = os.getenv("PI_HOST", "http://chotu.local:7000")
    state = run_launcher()                          # config screen → os.environ set
    plan = plan_services(state, REPO, pi_host)

    procs: list[subprocess.Popen] = []
    if plan.spawn_llama:
        print("  starting llama-server …  (logs: out/llama.log)")
        procs.append(spawn_llama(plan.llama_args, REPO / "out" / "llama.log"))
    if plan.start_bridge:
        print("  starting Pi bridge over SSH …  (logs: out/bridge.log)")
        spawn_bridge(REPO / "out" / "bridge.log")   # fire-and-forget; left running on exit
    if not plan.spawn_llama:                        # DashScope + Claude are both cloud
        print("  cloud preset selected — this spends tokens.")

    if plan.health_checks:
        print(f"  waiting for: {', '.join(n for n, _ in plan.health_checks)} …")
        try:
            wait_healthy(plan.health_checks)
        except TimeoutError as e:
            print(f"  ✗ {e}")
            teardown(procs)
            sys.exit(1)

    os.environ["PALIV_NO_LAUNCHER"] = "1"
    import core.brain                                # late import: brain reads env at import time
    try:
        asyncio.run(core.brain.main())
    finally:
        teardown(procs)                              # kills llama; bridge left running


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the module imports and the suite stays green**

Run: `.venv/bin/python -c "import core.run; print('ok')" && .venv/bin/python -m pytest -q`
Expected: `ok`; `176 passed, 3 skipped` (plus the new `test_run`/`test_launcher` tests → higher pass count, never lower).

- [ ] **Step 3: Create `launch.sh`**

Create `launch.sh`:

```bash
#!/usr/bin/env bash
cd "$(dirname "$0")"
source .venv/bin/activate
exec python -m core.run "$@"
```

Then: `chmod +x launch.sh`

- [ ] **Step 4: Manual smoke (offline, no services)**

Run: `PALIV_NO_LAUNCHER=1` is not set here; instead verify the planner path without a TTY by importing:
Run: `.venv/bin/python -c "from pathlib import Path; from core.launcher import LauncherState; from core.run import plan_services; print(plan_services(LauncherState(preset_idx=3, pi_mode='offline'), Path('.'), 'http://x:7000'))"`
Expected: prints a `ServicePlan(spawn_llama=False, llama_args=[], health_checks=[], start_bridge=False)`.

(Full end-to-end — real `./launch.sh` starting llama + brain — is a user-driven verification; it spawns a real llama-server and the interactive brain, so it is not part of the automated suite.)

- [ ] **Step 5: Commit**

```bash
git add core/run.py launch.sh
git commit -m "feat(run): main() orchestration wiring + launch.sh entrypoint"
```

---

### Task 7: Docs — DEV.md run section + REPO_MAP

**Files:**
- Modify: `docs/DEV.md` (run commands), `docs/REPO_MAP.md` (add `core/run.py`, `launch.sh`)

**Interfaces:** none (docs only).

- [ ] **Step 1: Update `docs/DEV.md`**

In the run/start section, add the new one-command path and note the old manual commands still work:

```markdown
## Launch (one command)

    ./launch.sh

Opens the config screen (model preset · persona · input · mute/debug/stats ·
Pi-bridge mode), then starts llama-server (local presets) and optionally the Pi
bridge, health-gates them, and runs the brain in this terminal. llama-server is
torn down on exit; the Pi bridge is left running. Service logs: `out/llama.log`,
`out/bridge.log`. Manual per-process commands (below) still work for debugging.
```

- [ ] **Step 2: Update `docs/REPO_MAP.md`**

Add to the `core/` table:

```markdown
| `run.py` | Launcher orchestrator (`./launch.sh` → `python -m core.run`): config screen → spawn llama-server/Pi bridge → health-gate → run brain foreground → teardown llama on exit. |
| `launcher.py` | Pre-launch TTY config screen: presets + persona/input/mute/debug/stats/Pi-bridge; returns a `LauncherState`. |
```

And under "Tests, docs, assets" add: `` `launch.sh` — repo-root entrypoint: activates the venv and runs `python -m core.run`. ``

- [ ] **Step 3: Verify suite still green**

Run: `.venv/bin/python -m pytest -q`
Expected: `176 passed, 3 skipped` + new tests.

- [ ] **Step 4: Commit**

```bash
git add docs/DEV.md docs/REPO_MAP.md
git commit -m "docs: document ./launch.sh and add run.py/launch.sh to REPO_MAP"
```

---

## Self-Review

**Spec coverage:** upgraded screen + 4 presets (T1–2), local-URL override (T2 `to_env`), input radio (T2), stats toggle (T2), Pi-bridge 3-way (T2 + T4 planner), `run_launcher` returns state (T3), llama spawn + health gate + foreground brain + teardown (T4–6), `launch.sh` (T6), DashScope-leaves-URL + Claude provider (T2 tests), docs (T7). All spec sections mapped.

**Placeholder scan:** no "TBD"/"handle errors"/"similar to Task N"; every code step shows full code; health-timeout, SSH-fail (warn+continue via graceful brain), and cloud-notice paths are concrete in `main()`.

**Type consistency:** `LauncherState` field names (`input_mode`, `pi_mode`, `stats`), row constants, `PRESETS` keys (`spawn_llama`, `mmproj`, `extra`), `llama_args(preset, models_dir)`, `ServicePlan` fields, and `plan_services(state, repo_root, pi_host)` are used identically across Tasks 1–6. `LLAMA_HEALTH`/`http://127.0.0.1:8080` is one constant. The cloud-notice in `main()` fires for any non-`spawn_llama` preset, correctly covering both DashScope and Claude.
