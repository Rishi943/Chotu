# Launch Config Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an interactive, arrow-key terminal screen before `python -m core.brain` starts its loop, letting the user pick model/provider preset and toggle mute/debug/voice/PTT/persona, with choices applied via `os.environ`.

**Architecture:** A new `core/launcher.py` separates pure state logic (`LauncherState` — seed from env, handle keys, emit env dict) from a thin `termios`/`tty` raw-mode render/read wrapper. `core/brain.py` calls `run_launcher()` once at the very top of `__main__` execution — before any env-reading `core.*` import — so the chosen values win over `.env` (which loads with `override=False`). Stdlib only; no new dependency.

**Tech Stack:** Python 3.12 stdlib (`os`, `sys`, `termios`, `tty`), pytest 9.

---

## File Structure

- **Create `core/launcher.py`** — all launcher logic:
  - `PRESETS` — ordered preset table (label → provider/model/tag).
  - `LauncherState` — dataclass holding `preset_idx`, toggle booleans, `persona`, and `focus`; pure methods `seed_from_env`, `apply_key`, `to_env`, plus a `rows()`/render helper returning the screen as text.
  - `run_launcher()` — bypass checks + raw-mode I/O loop driving `LauncherState`, then writes `os.environ`.
- **Create `tests/test_launcher.py`** — unit tests for the pure parts (`seed_from_env`, `apply_key`, `to_env`). No TTY needed.
- **Modify `core/brain.py`** — insert a 3-line `run_launcher()` call between the stdlib imports and the first `from core.*` import.

All env-var names, preset filenames, and key mappings come from the design spec
`docs/superpowers/specs/2026-06-16-launch-config-screen-design.md`.

---

## Task 1: Preset table + `LauncherState` skeleton with `to_env`

**Files:**
- Create: `core/launcher.py`
- Test: `tests/test_launcher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_launcher.py
"""Unit tests for the pre-launch config screen (pure-logic parts only)."""

from core.launcher import LauncherState, PRESETS


def test_presets_order_and_content():
    labels = [p["label"] for p in PRESETS]
    assert labels == ["Gemma", "Qwen", "Claude"]
    gemma, qwen, claude = PRESETS
    assert gemma["provider"] == "local"
    assert gemma["model"] == "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    assert qwen["provider"] == "local"
    assert qwen["model"] == "Qwen3.5-4B-Q4_K_M.gguf"
    assert claude["provider"] == "claude"
    assert claude["model"] == "claude-sonnet-4-6"


def test_to_env_defaults_qwen_base_all_off():
    s = LauncherState()  # preset_idx defaults to 1 (Qwen), all toggles off, base persona
    env = s.to_env()
    assert env["PALIV_LLM_PROVIDER"] == "local"
    assert env["PALIV_BRAIN_MODEL"] == "Qwen3.5-4B-Q4_K_M.gguf"
    assert env["PALIV_MUTE"] == "0"
    assert env["PALIV_DEBUG"] == "0"
    assert env["PALIV_VOICE"] == "0"
    assert env["PALIV_PTT"] == "0"
    assert env["PALIV_PERSONA"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_launcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.launcher'`.

- [ ] **Step 3: Write minimal implementation**

```python
# core/launcher.py
"""Pre-launch interactive config screen for `python -m core.brain`.

Renders an arrow-key terminal screen (stdlib termios/tty only) letting the user
pick a model/provider preset and toggle mute/debug/voice/PTT/persona, then writes
the choices into os.environ. Must run BEFORE any env-reading core.* import so the
selections win over .env (which loads with override=False)."""

import os
import sys
from dataclasses import dataclass

PRESETS = [
    {"label": "Gemma",  "provider": "local",  "model": "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf", "tag": "local"},
    {"label": "Qwen",   "provider": "local",  "model": "Qwen3.5-4B-Q4_K_M.gguf",             "tag": "local"},
    {"label": "Claude", "provider": "claude", "model": "claude-sonnet-4-6",                  "tag": "cloud — spends tokens"},
]

# Toggle field name -> env var.
TOGGLES = {"mute": "PALIV_MUTE", "debug": "PALIV_DEBUG", "voice": "PALIV_VOICE", "ptt": "PALIV_PTT"}


@dataclass
class LauncherState:
    preset_idx: int = 1            # default highlight: Qwen
    mute: bool = False
    debug: bool = False
    voice: bool = False
    ptt: bool = False
    persona: str = "base"          # "base" or "reel"
    focus: int = 0                 # index into the focusable row list

    def to_env(self) -> dict[str, str]:
        preset = PRESETS[self.preset_idx]
        env = {
            "PALIV_LLM_PROVIDER": preset["provider"],
            "PALIV_BRAIN_MODEL": preset["model"],
            "PALIV_PERSONA": "reel" if self.persona == "reel" else "",
        }
        for field_name, var in TOGGLES.items():
            env[var] = "1" if getattr(self, field_name) else "0"
        return env
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_launcher.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add core/launcher.py tests/test_launcher.py
git commit -m "feat(launcher): preset table + LauncherState.to_env"
```

---

## Task 2: `seed_from_env` — defaults from current environment

**Files:**
- Modify: `core/launcher.py`
- Test: `tests/test_launcher.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_launcher.py
def test_seed_empty_env_is_qwen_base_off():
    s = LauncherState.seed_from_env({})
    assert s.preset_idx == 1        # Qwen
    assert s.persona == "base"
    assert not (s.mute or s.debug or s.voice or s.ptt)


def test_seed_mute_flag_checks_mute():
    s = LauncherState.seed_from_env({"PALIV_MUTE": "1"})
    assert s.mute is True
    assert s.debug is False


def test_seed_claude_provider_selects_claude():
    s = LauncherState.seed_from_env({"PALIV_LLM_PROVIDER": "claude"})
    assert s.preset_idx == 2        # Claude


def test_seed_gemma_model_selects_gemma():
    s = LauncherState.seed_from_env(
        {"PALIV_LLM_PROVIDER": "local", "PALIV_BRAIN_MODEL": "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"})
    assert s.preset_idx == 0        # Gemma


def test_seed_reel_persona():
    s = LauncherState.seed_from_env({"PALIV_PERSONA": "reel"})
    assert s.persona == "reel"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_launcher.py -k seed -v`
Expected: FAIL with `AttributeError: type object 'LauncherState' has no attribute 'seed_from_env'`.

- [ ] **Step 3: Write minimal implementation**

Add this classmethod to `LauncherState` in `core/launcher.py`:

```python
    @classmethod
    def seed_from_env(cls, env: dict[str, str]) -> "LauncherState":
        provider = env.get("PALIV_LLM_PROVIDER", "local")
        model = env.get("PALIV_BRAIN_MODEL", "")
        if provider == "claude":
            preset_idx = 2
        elif "gemma" in model.lower():
            preset_idx = 0
        else:
            preset_idx = 1  # default: Qwen
        return cls(
            preset_idx=preset_idx,
            mute=env.get("PALIV_MUTE") == "1",
            debug=env.get("PALIV_DEBUG") == "1",
            voice=env.get("PALIV_VOICE") == "1",
            ptt=env.get("PALIV_PTT") == "1",
            persona="reel" if env.get("PALIV_PERSONA") == "reel" else "base",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_launcher.py -k seed -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add core/launcher.py tests/test_launcher.py
git commit -m "feat(launcher): seed LauncherState from environment"
```

---

## Task 3: `apply_key` — focus movement, toggles, radio, persona cycle

**Files:**
- Modify: `core/launcher.py`
- Test: `tests/test_launcher.py`

The focusable rows, in order, are: 3 preset rows (focus 0,1,2), then mute (3),
debug (4), voice (5), ptt (6), persona (7), and `Start` (8) — 9 rows total.
`apply_key` returns `("continue", state)`, `("start", state)`, or `("quit", state)`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_launcher.py
def test_down_moves_focus_and_wraps():
    s = LauncherState(focus=0)
    action, s = s.apply_key("DOWN")
    assert action == "continue" and s.focus == 1
    s = LauncherState(focus=8)
    _, s = s.apply_key("DOWN")
    assert s.focus == 0


def test_up_wraps_to_last():
    s = LauncherState(focus=0)
    _, s = s.apply_key("UP")
    assert s.focus == 8


def test_select_on_preset_row_is_radio():
    s = LauncherState(focus=0, preset_idx=1)   # focus on Gemma row
    _, s = s.apply_key("SELECT")
    assert s.preset_idx == 0                    # selecting Gemma replaces Qwen


def test_select_on_toggle_flips_only_that_toggle():
    s = LauncherState(focus=3)                  # mute row
    _, s = s.apply_key("SELECT")
    assert s.mute is True and s.debug is False
    _, s = s.apply_key("SELECT")
    assert s.mute is False


def test_select_on_persona_cycles():
    s = LauncherState(focus=7, persona="base")
    _, s = s.apply_key("SELECT")
    assert s.persona == "reel"
    _, s = s.apply_key("SELECT")
    assert s.persona == "base"


def test_select_on_start_returns_start():
    s = LauncherState(focus=8)
    action, _ = s.apply_key("SELECT")
    assert action == "start"


def test_quit_key_returns_quit():
    action, _ = LauncherState().apply_key("QUIT")
    assert action == "quit"


def test_unknown_key_is_noop():
    s = LauncherState(focus=2)
    action, s2 = s.apply_key("?")
    assert action == "continue" and s2.focus == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_launcher.py -k "focus or radio or toggle or persona or start or quit or unknown" -v`
Expected: FAIL with `AttributeError: 'LauncherState' object has no attribute 'apply_key'`.

- [ ] **Step 3: Write minimal implementation**

Add a module-level constant and the method to `core/launcher.py`:

```python
N_ROWS = 9          # 3 presets + mute/debug/voice/ptt + persona + Start
START_ROW = 8
PERSONA_ROW = 7
PRESET_ROWS = (0, 1, 2)
TOGGLE_BY_ROW = {3: "mute", 4: "debug", 5: "voice", 6: "ptt"}
```

```python
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
            elif self.focus == PERSONA_ROW:
                self.persona = "reel" if self.persona == "base" else "base"
            return ("continue", self)
        return ("continue", self)   # unknown key: no-op
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_launcher.py -v`
Expected: PASS (all tests, ~15 passed).

- [ ] **Step 5: Commit**

```bash
git add core/launcher.py tests/test_launcher.py
git commit -m "feat(launcher): apply_key state transitions"
```

---

## Task 4: `render()` text + `run_launcher()` raw-mode I/O and bypass

**Files:**
- Modify: `core/launcher.py`
- Test: `tests/test_launcher.py`

- [ ] **Step 1: Write the failing test**

Only the pure `render()` and the bypass branch are unit-tested; the raw-mode key
loop is verified manually (Step 6).

```python
# append to tests/test_launcher.py
import core.launcher as launcher


def test_render_shows_selected_preset_and_toggles():
    s = LauncherState(preset_idx=0, mute=True, persona="reel", focus=0)
    text = s.render()
    assert "Gemma" in text and "Qwen" in text and "Claude" in text
    assert "(•) Gemma" in text          # Gemma selected
    assert "[✓] Mute" in text           # mute on
    assert "reel" in text               # persona shown


def test_run_launcher_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("PALIV_NO_LAUNCHER", "1")
    monkeypatch.setenv("PALIV_BRAIN_MODEL", "sentinel-unchanged")
    launcher.run_launcher()
    assert os.environ["PALIV_BRAIN_MODEL"] == "sentinel-unchanged"


def test_run_launcher_noop_when_not_tty(monkeypatch):
    monkeypatch.delenv("PALIV_NO_LAUNCHER", raising=False)
    monkeypatch.setattr(launcher.sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("PALIV_BRAIN_MODEL", "sentinel-unchanged")
    launcher.run_launcher()
    assert os.environ["PALIV_BRAIN_MODEL"] == "sentinel-unchanged"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_launcher.py -k "render or noop" -v`
Expected: FAIL with `AttributeError: 'LauncherState' object has no attribute 'render'`.

- [ ] **Step 3: Write minimal implementation**

Add `render()` to `LauncherState` and the module-level `_read_key` / `run_launcher`
to `core/launcher.py`:

```python
    def render(self) -> str:
        def cur(row): return "›" if self.focus == row else " "
        lines = ["  Chotu brain — launch config        (↑/↓ move · space toggle · enter start · q quit)", ""]
        lines.append("  Model:")
        for i, p in enumerate(PRESETS):
            mark = "•" if self.preset_idx == i else " "
            tag = f"  ({p['tag']})" if p["tag"] != "local" else "  (local)"
            lines.append(f"  {cur(i)} ({mark}) {p['label']}{tag}")
            if self.preset_idx == i and p["provider"] == "local":
                lines.append("        ↳ launch llama-server with this gguf")
        lines.append("")
        for row, name, label in [(3, "mute", "Mute"), (4, "debug", "Debug"),
                                 (5, "voice", "Voice"), (6, "ptt", "PTT")]:
            box = "✓" if getattr(self, name) else " "
            lines.append(f"  {cur(row)} [{box}] {label}")
        lines.append(f"  {cur(PERSONA_ROW)} Persona: {self.persona} ▸")
        lines.append("")
        lines.append(f"  {cur(START_ROW)} Start ▶")
        return "\n".join(lines)


def _read_key() -> str:
    """Block for one keystroke (raw mode already active). Map to a logical key."""
    ch = sys.stdin.read(1)
    if ch == "\x1b":                       # ESC — maybe an arrow sequence
        seq = sys.stdin.read(2)
        if seq == "[A":
            return "UP"
        if seq == "[B":
            return "DOWN"
        return "QUIT"                      # bare ESC quits
    if ch in ("\r", "\n", " "):
        return "SELECT" if ch == " " else "ENTER_OR_SELECT"
    if ch in ("q", "Q"):
        return "QUIT"
    if ch == "\x03":                       # Ctrl-C
        return "QUIT"
    return ch


def run_launcher() -> None:
    """Interactive pre-launch config screen. Mutates os.environ in place.
    No-op when stdin is not a TTY or PALIV_NO_LAUNCHER=1."""
    if os.getenv("PALIV_NO_LAUNCHER") == "1" or not sys.stdin.isatty():
        return

    import termios
    import tty

    state = LauncherState.seed_from_env(dict(os.environ))
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            sys.stdout.write("\x1b[2J\x1b[H")        # clear + home
            sys.stdout.write(state.render().replace("\n", "\r\n"))
            sys.stdout.flush()
            key = _read_key()
            # Both Space and Enter act as SELECT in the model; only Enter on the
            # Start row starts. apply_key treats "SELECT" uniformly, so normalise.
            logical = "SELECT" if key in ("SELECT", "ENTER_OR_SELECT") else key
            action, state = state.apply_key(logical)
            if action == "start":
                break
            if action == "quit":
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                sys.stdout.write("\r\n")
                sys.exit(0)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\r\n")
        sys.stdout.flush()

    for var, val in state.to_env().items():
        os.environ[var] = val
```

Note: `_read_key` returns `"ENTER_OR_SELECT"` for Enter and `"SELECT"` for Space;
`run_launcher` normalises both to `"SELECT"` before calling `apply_key`, matching
the `apply_key` tests in Task 3 (which only ever pass `"SELECT"`).

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_launcher.py -v`
Expected: PASS (all tests pass, ~17 total).

- [ ] **Step 5: Commit**

```bash
git add core/launcher.py tests/test_launcher.py
git commit -m "feat(launcher): render + raw-mode run_launcher with bypass"
```

- [ ] **Step 6: Manual smoke test of the live screen**

Run: `source .venv/bin/activate && PALIV_NO_LAUNCHER=1 python -c "from core.launcher import run_launcher; run_launcher(); print('bypassed ok')"`
Expected: prints `bypassed ok` with no screen (bypass path).

Then, in a real terminal, run the interactive screen in isolation (does NOT start
the brain or touch hardware):
Run: `source .venv/bin/activate && python -c "from core.launcher import run_launcher; import os; run_launcher(); print(os.environ['PALIV_LLM_PROVIDER'], os.environ['PALIV_BRAIN_MODEL'], os.environ['PALIV_MUTE'])"`
Expected: arrow keys move the `›` cursor; Space toggles boxes / selects preset;
Enter on `Start ▶` exits and prints the chosen provider/model/mute; `q` exits
silently with the terminal restored (no stuck/garbled terminal).

---

## Task 5: Wire `run_launcher()` into `core/brain.py`

**Files:**
- Modify: `core/brain.py` (insert after line 14, before line 16)

- [ ] **Step 1: Make the edit**

In `core/brain.py`, between `from dotenv import load_dotenv` (line 14) and
`from core.llm_client import LLMClient` (line 16), insert:

```python

# Pre-launch config screen — must run BEFORE the env-reading core.* imports below
# so the user's picks win over .env (load_dotenv uses override=False). No-op for
# non-TTY / PALIV_NO_LAUNCHER=1 / when imported as a module (chotu skill, dry_run).
if __name__ == "__main__":
    from core.launcher import run_launcher
    run_launcher()
```

- [ ] **Step 2: Verify imports still resolve and module imports cleanly**

Run: `source .venv/bin/activate && python -c "import core.brain; print('import ok')"`
Expected: prints `import ok` (importing as a module does NOT trigger the launcher,
because `__name__ != "__main__"`).

- [ ] **Step 3: Verify the existing test suite still passes**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: PASS — no regressions (same pass/fail set as before this plan, plus the
new `tests/test_launcher.py`).

- [ ] **Step 4: End-to-end bypass check**

Run: `source .venv/bin/activate && PALIV_NO_LAUNCHER=1 PALIV_MUTE=1 PALIV_DEBUG=1 timeout 6 python -m core.brain </dev/null 2>&1 | head -5`
Expected: the launcher is skipped (non-TTY stdin + `PALIV_NO_LAUNCHER=1`) and the
brain prints its normal `Chotu brain started (model: ..., provider: ...)` line,
confirming the insert does not break the non-interactive path.

- [ ] **Step 5: Commit**

```bash
git add core/brain.py
git commit -m "feat(brain): show launch config screen before loop starts"
```

---

## Self-Review Notes

- **Spec coverage:** bypass (Task 4), seed-from-env defaults incl. `PALIV_MUTE=1`
  example (Task 2/4), preset table + tags (Task 1/4), toggles + persona (Task 3),
  render w/ llama-server reminder + cloud tag (Task 4), brain wiring before
  env-reading imports (Task 5). Out-of-scope items (.env persistence, llama-server
  control) intentionally absent.
- **No placeholders:** every code step shows full code; commands have expected
  output.
- **Type consistency:** `LauncherState` fields (`preset_idx`, `mute`, `debug`,
  `voice`, `ptt`, `persona`, `focus`), methods (`seed_from_env`, `apply_key`,
  `to_env`, `render`), and the row constants (`N_ROWS=9`, `START_ROW=8`,
  `PERSONA_ROW=7`, `PRESET_ROWS`, `TOGGLE_BY_ROW`) are used consistently across
  Tasks 1–5. `apply_key` only ever receives `"SELECT"` (Space/Enter normalised in
  `run_launcher`), matching its tests.
```
