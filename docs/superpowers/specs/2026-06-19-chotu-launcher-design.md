# Chotu launcher / orchestrator — design

**Date:** 2026-06-19
**Status:** approved, ready for implementation plan
**Scope:** One terminal command that configures + starts the whole Chotu stack.
This is sub-project **A**. The brain terminal-UI overhaul (perf stats, spinner,
streaming thinking, input queue, readability) is sub-project **B** — out of
scope here except for one coupling: A sets `PALIV_SHOW_STATS`, B consumes it.

## Goal

Replace the manual three-terminal dance (SSH Pi bridge + `llama-server …` +
`python -m core.brain`) with a single `./launch.sh` that:

1. Shows an upgraded TTY config screen (preset · persona · input · mute · debug ·
   stats · Pi-bridge mode).
2. Starts the services the chosen config needs (local `llama-server`; optionally
   the Pi bridge over SSH), logging them to `out/`.
3. Health-gates on those services, then runs the brain in the **foreground**
   (it's interactive — reads terminal input) reusing this terminal.
4. On exit, tears down the `llama-server` **it** started; leaves the Pi bridge
   running.

## Decisions (locked with the user)

- **Form:** extend the existing `core/launcher.py` TTY screen (don't rewrite) +
  new `core/run.py` orchestrator + `launch.sh` wrapper.
- **Layout:** single terminal — services run in the background to `out/*.log`,
  brain in front.
- **Pi bridge:** 3-way radio — `Start via SSH` · `Already running` · `Offline`.
  Do **not** stop the bridge on exit (only `llama-server` is torn down).
- **Presets (4):** Gemma local · Qwen local · **Qwen cloud (DashScope)** · Claude.
- **Input:** merge the old Voice + PTT checkboxes into one 3-way radio —
  `Text · Voice · PTT` (no meaningless both-on state).
- **Dropped:** `--goal` (the brain has no `argparse`/`sys.argv` — it was a no-op).
- **Stats toggle:** new on-screen toggle → `PALIV_SHOW_STATS` for B.

## Constraints (verified)

- **Cloud presets spend tokens** only when the *user* picks them and then
  interacts — that's explicit user choice, allowed. The launcher itself makes
  **no** cloud probe calls (health-check only hits local llama + the Pi).
- **`.env` currently points `PALIV_BRAIN_URL` at DashScope.** So for **local**
  presets, `to_env()` must override `PALIV_BRAIN_URL` → `http://127.0.0.1:8080/v1`
  and clear `PALIV_BRAIN_KEY`, else a "local" run silently hits the cloud. For
  the **DashScope** preset, leave URL/KEY untouched (supplied by `.env`).
- **Brain reads env at import time** (`MUTE`, `PI_HOST`, `dispatch_map`,
  `pi = PiClient(PI_HOST)` …). So `core/run.py` must set env **before** importing
  `core.brain`. `load_dotenv(override=False)` means launcher-set vars win.
- **Entrypoint:** `core.brain` exposes `async def main()` (brain.py:474) called by
  `asyncio.run(main())` under `__main__`. `core/run.py` will, after env+services
  are ready, set `PALIV_NO_LAUNCHER=1`, `import core.brain`, and
  `asyncio.run(core.brain.main())`. The `__main__` block that calls
  `run_launcher()` is skipped on import — no double launcher.
- No new framework: orchestration uses stdlib `subprocess`/`signal` + `httpx`
  (already a dep) for health polls.

## Components

### 1. `core/launcher.py` (upgraded)

`PRESETS` grows to 4 entries; **local** presets additionally carry the
`llama-server` invocation pieces (gguf, mmproj, port, and the flag list — Gemma
and Qwen differ: Gemma uses `--swa-full --reasoning-budget -1` + `gemma_mmproj`;
Qwen uses `mmproj-BF16` and no `--swa-full`). Captured per-preset as a
`llama_args(repo_root) -> list[str]` builder.

`LauncherState` changes:
- `input_mode: str` (`"text"|"voice"|"ptt"`) replaces `voice`/`ptt` bools.
- add `stats: bool`, `pi_mode: str` (`"start"|"running"|"offline"`).
- Row constants (`N_ROWS`, `*_ROW`, `TOGGLE_BY_ROW`) updated for the new layout:
  Model(4) · Persona · Input · Mute · Debug · Stats · Pi-bridge · Start.
- `apply_key` cycles the new radios (input, pi_mode) and toggles stats.
- `seed_from_env` seeds `input_mode` from `PALIV_VOICE`/`PALIV_PTT`, `stats` from
  `PALIV_SHOW_STATS`, `pi_mode` default `"running"`.
- `to_env()` emits:
  - `PALIV_LLM_PROVIDER`, `PALIV_BRAIN_MODEL` from preset.
  - For local presets: `PALIV_BRAIN_URL=http://127.0.0.1:8080/v1`, `PALIV_BRAIN_KEY=""`.
  - For DashScope preset: provider `local`, model e.g. `qwen3.5-flash`, URL/KEY left unset.
  - For Claude preset: provider `claude`, model `claude-sonnet-4-6`.
  - `PALIV_VOICE`/`PALIV_PTT` from `input_mode`; `PALIV_MUTE`, `PALIV_DEBUG`,
    `PALIV_SHOW_STATS`, `PALIV_PERSONA`.
- `run_launcher()` **returns the final `LauncherState`** (in addition to mutating
  `os.environ`) so `core/run.py` can read `preset`, `pi_mode`, and `llama_args`.
  Today it returns `None`; the no-op/early-exit paths return a sentinel state
  seeded from env so `run.py` still has a plan when the screen is skipped.

### 2. `core/run.py` (new) — orchestrator

Thin and dependency-injected so it's testable:

```
def main():
    load_dotenv()                       # PI_HOST, DashScope creds for orchestration
    state = run_launcher()              # screen → os.environ set, returns plan
    procs = []
    if preset.is_local:
        procs.append(spawn_llama(state.llama_args(REPO), log=out/"llama.log"))
    if state.pi_mode == "start":
        spawn_bridge(log=out/"bridge.log")          # ssh … sudo … server.py (fire-and-forget)
    wait_healthy(llama_url if local, pi_url unless offline)   # poll /health, timeout+message
    os.environ["PALIV_NO_LAUNCHER"] = "1"
    import core.brain
    try:
        asyncio.run(core.brain.main())
    finally:
        teardown(procs)                 # SIGTERM→wait→SIGKILL the llama proc; bridge left alone
```

- `spawn_llama(args, log)` / `spawn_bridge(log)` / `wait_healthy(checks)` /
  `teardown(procs)` are module functions; `wait_healthy` takes an injectable
  `probe(url)->bool` so tests use a fake.
- `spawn_bridge` runs `ssh chotu@chotu.local 'sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py'`
  detached, stdout/err → `out/bridge.log`. Passwordless sudo assumed; if SSH
  exits nonzero quickly, warn and continue (brain degrades gracefully when the
  Pi is unreachable).

### 3. `launch.sh` (new)

```bash
#!/usr/bin/env bash
cd "$(dirname "$0")"
source .venv/bin/activate
exec python -m core.run "$@"
```

## Failure handling

- **llama health timeout:** print error + last lines of `out/llama.log`, kill the
  spawned proc, abort before starting the brain.
- **Pi bridge SSH fails (mode=start):** warn, continue (brain handles
  Pi-unreachable). `Offline` mode skips the Pi health-check entirely.
- **Cloud preset:** no llama spawn; print a one-line "this spends tokens" notice.

## Testing

- `tests/test_launcher.py` (extend — current 3-tuple `gemma, qwen, claude =
  PRESETS` unpack and row-index asserts **will** need updating):
  - `to_env()` per preset: local → localhost URL + empty key; DashScope → URL/KEY
    untouched + provider local; Claude → provider claude.
  - input radio → `(PALIV_VOICE, PALIV_PTT)` mapping for text/voice/ptt.
  - stats toggle and pi_mode radio via `apply_key`; updated focus/row wrapping.
  - `seed_from_env` round-trips the new fields.
- `tests/test_run.py` (new) for `core/run.py` with injected fakes:
  - local preset → `spawn_llama` called with the preset's args; cloud → not called.
  - `wait_healthy` polls until the fake probe returns True; times out → raises.
  - `teardown` terminates spawned procs; bridge proc (if any) is left.
  - brain import happens only after `PALIV_NO_LAUNCHER=1` is set (assert ordering
    via a fake `core.brain.main`).
- Baseline gate: full suite stays green (currently 176 passed / 3 skipped).

## Out of scope (→ sub-project B)

Terminal-UI overhaul: perf-stat rendering, await-tool spinner, live-streamed
thinking styled vs speak, all-tool-call display, input queue with sent/queued
indicators, vertical spacing/readability, optional sixel cam panel, Rich-vs-
hand-rolled decision. A only *sets* `PALIV_SHOW_STATS`.
