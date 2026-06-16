# Pre-launch config screen for `core.brain`

**Date:** 2026-06-16
**Status:** Approved design, pending implementation plan

## Problem

`python -m core.brain` reads model/provider/flags from the environment (`.env` +
inline `PALIV_*` vars) at import time. To switch from Qwen to Gemma, or to flip
mute/debug/voice/PTT, the user must remember exact env-var names and quant
filenames and type them on the command line every launch. There is no way to see
or change the active config interactively before the loop starts.

Goal: launch `python -m core.brain` with no flags, see an interactive screen of
toggles (like Claude Code settings), adjust them with the keyboard, then start
the loop — all without editing files or memorising env-var names.

## Scope

In scope:
- A pre-launch, arrow-key terminal screen that sets provider/model via presets
  plus toggles for mute, debug, voice, PTT, and persona.
- Seeding the screen's defaults from the current environment.
- Mutating `os.environ` so the existing brain code paths pick up the choices.

Explicitly out of scope:
- Starting/stopping/relaunching `llama-server`. For local presets the launcher
  only sets the model **label** sent to `llama-server`; the user launches
  `llama-server` with the matching `.gguf` themselves.
- Persisting choices to `.env` (decided: do **not** persist; `.env` is read-only
  seed for defaults).
- Editing arbitrary `PALIV_*` vars. Only the fields listed below are exposed.
- Any change to the running loop, GUI, or tool dispatch.

## Key constraint: import-time env reads

`core/prompts.py` computes `SYSTEM_PROMPT` at import (reads `PALIV_PERSONA`), and
`core/brain.py` reads `PALIV_DEBUG/MUTE/VOICE/PTT/...` plus constructs
`LLMClient()` (reads `PALIV_LLM_PROVIDER`, `PALIV_BRAIN_MODEL`) during module
load. `load_dotenv()` runs with `override=False`, so any value already present in
`os.environ` is **not** overwritten by `.env`.

Therefore the launcher must run, and mutate `os.environ`, **before** the first
`from core.* import ...` in `brain.py`. Values it sets then take precedence over
`.env` for free, and all existing read sites work unchanged.

## Components

### `core/launcher.py` (new)

Single public function:

```python
def run_launcher() -> None:
    """Interactive pre-launch config screen. Mutates os.environ in place.
    No-op when stdin is not a TTY or PALIV_NO_LAUNCHER=1."""
```

Behaviour:
1. **Bypass.** If `not sys.stdin.isatty()` or `os.getenv("PALIV_NO_LAUNCHER") == "1"`,
   return immediately (covers the `chotu` skill, `dry_run`, pipes, CI, and an
   explicit opt-out). The skill and `dry_run` import `core.brain` rather than run
   it as `__main__`, so they never reach `run_launcher()` anyway; the TTY/opt-out
   checks are belt-and-suspenders for direct piped runs.
2. **Seed defaults from env** (read at call time, before `.env` is loaded — so the
   seed is whatever the user passed inline; if unset, the field's built-in
   default is shown and the unset var is left for `.env`/code defaults to fill):
   - Model preset: infer from `PALIV_LLM_PROVIDER` + `PALIV_BRAIN_MODEL`
     (provider `claude` → Claude; provider `local` + a Gemma model string →
     Gemma; otherwise Qwen as the default highlight).
   - Toggles: `PALIV_MUTE/DEBUG/VOICE/PTT` == `"1"`.
   - Persona: `PALIV_PERSONA` (`"reel"` → reel, else base).
3. **Render + input loop** in raw terminal mode (`termios`/`tty`, stdlib only):
   - Focusable rows: 3 model radio rows, the toggle/persona rows, and a final
     `Start ▶` row.
   - `↑`/`↓` (and their ANSI escape sequences `\x1b[A` / `\x1b[B`) move focus.
   - `Space`/`Enter` on a model row selects that preset (radio); on a toggle
     flips it; on the persona field cycles base ⇄ reel.
   - `Enter` on `Start ▶` exits the loop and applies choices.
   - `q` or `Ctrl-C` aborts the whole program cleanly (restore terminal, exit 0).
   - Terminal state is always restored via `try/finally` around `tty.setraw`.
4. **Apply.** On `Start`, write the resolved values into `os.environ`:
   - Preset → `PALIV_LLM_PROVIDER`, `PALIV_BRAIN_MODEL` (and only those; URL/key
     stay as `.env`/defaults).
   - Toggles → `PALIV_MUTE/DEBUG/VOICE/PTT` set to `"1"` or `"0"`.
   - Persona → `PALIV_PERSONA` set to `"reel"` or `""`.

### Preset table

| Label  | `PALIV_LLM_PROVIDER` | `PALIV_BRAIN_MODEL`                       | Tag             |
|--------|----------------------|------------------------------------------|-----------------|
| Gemma  | `local`              | `gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf`     | local           |
| Qwen   | `local`              | `Qwen3.5-4B-Q4_K_M.gguf`                 | local           |
| Claude | `claude`             | `claude-sonnet-4-6`                      | cloud — tokens  |

- Under a focused/selected **local** preset, render a dim reminder line:
  `↳ launch llama-server with this gguf`.
- The Claude row shows `(cloud — spends tokens)`. Selecting it interactively is
  the user's explicit per-launch approval, satisfying the CLAUDE.md cloud rule.

### Screen mock

```
  Chotu brain — launch config        (↑/↓ move · space toggle · enter start · q quit)

  Model:   ( ) Gemma  (local)
           (•) Qwen   (local)
             ↳ launch llama-server with this gguf
           ( ) Claude (cloud — spends tokens)

  [✓] Mute      [ ] Debug     [ ] Voice     [ ] PTT
  Persona: base ▸

  Start ▶
```

### `core/brain.py` (one edit)

Insert between the stdlib imports (after `from dotenv import load_dotenv`,
line 14) and the first `from core.* import` (line 16):

```python
if __name__ == "__main__":
    from core.launcher import run_launcher
    run_launcher()
```

`core/launcher.py` imports only stdlib (`os`, `sys`, `termios`, `tty`), so
importing it here does not trigger any env-dependent module load. No other line
in `brain.py` changes; the existing
`print(f"Chotu brain started (model: {llm_client.model}, provider: {llm_client.provider})")`
at line 472 then reflects the chosen values.

## Data flow

```
python -m core.brain
  └─ brain.py: if __name__=="__main__": run_launcher()
        └─ reads inline PALIV_* env as seed defaults
        └─ user picks preset + toggles
        └─ writes os.environ[...] = chosen values
  └─ from core.llm_client import LLMClient        # nothing read yet
  └─ from core.prompts import SYSTEM_PROMPT       # reads PALIV_PERSONA (now set)
  └─ load_dotenv(override=False)                  # does NOT clobber launcher values
  └─ MUTE/DEBUG/... = os.getenv(...)              # picks up launcher values
  └─ llm_client = LLMClient()                     # reads provider/model (now set)
  └─ asyncio.run(main())                          # normal loop
```

## Error handling

- Raw-mode entry/exit wrapped in `try/finally`; terminal always restored even on
  exception or `Ctrl-C`.
- Non-TTY / `PALIV_NO_LAUNCHER=1` → silent no-op, identical to today's behaviour.
- Unknown / unexpected keys are ignored (loop continues).
- `q`/`Ctrl-C` during the launcher → restore terminal, `sys.exit(0)` (do not fall
  through into the brain loop).

## Testing

Pure-logic parts are unit-testable without a TTY by factoring rendering/state
away from raw I/O:
- A `LauncherState` (or equivalent) holding preset index + toggle booleans +
  persona, with:
  - `seed_from_env(env: dict) -> LauncherState` — assert correct defaults for
    representative env dicts (empty, `PALIV_MUTE=1`, Claude provider, Gemma model,
    `PALIV_PERSONA=reel`).
  - `to_env() -> dict[str, str]` — assert each preset/toggle/persona maps to the
    exact env vars in the preset table.
  - A key-handler step `apply_key(state, key) -> state` — assert ↑/↓ wraps focus,
    space toggles, preset selection is radio (mutually exclusive), persona cycles.
- The raw-terminal render/read wrapper (`termios` I/O) is thin and verified
  manually (it only translates keystrokes into `apply_key` calls and prints the
  rendered frame).

Manual verification:
- `python -m core.brain` shows the screen; arrow keys + space behave; `Start`
  launches with the chosen model/provider echoed in the startup line.
- `PALIV_MUTE=1 python -m core.brain` shows Mute pre-checked.
- `PALIV_NO_LAUNCHER=1 python -m core.brain` skips the screen entirely.
- `echo | python -m core.brain` (non-TTY) skips the screen.

## YAGNI notes

- No `.env` writing, no llama-server control, no free-form env editor, no config
  file format — all deferred/declined.
- No new dependency: stdlib `termios`/`tty` only.
```
