# REPO_MAP

Per-file index for fast orientation — one line per source file, so an agent
reads this instead of grepping the tree. Keep in sync when files move or land.
The authoritative contracts are still `PALIV.md` + `CHOTU_BASE.md` (system
prompt) and `CLAUDE.md` (working rules); this is just the file directory.

Conventions: laptop = the brain host; Pi = the bridge host. Run-outputs go to
gitignored `out/`. Animations live in `assets/Animations/` (name is hardcoded).

## core/ — laptop brain (the live loop)

| File | Purpose |
|---|---|
| `brain.py` | Live paced loop: memory window, terminal/voice input, LLM call, tool dispatch, session save. Entry point (`python -m core.brain`). |
| `prompts.py` | Composes `PALIV.md` + persona (`CHOTU_BASE.md`/`CHOTU_REEL.md`) into `SYSTEM_PROMPT`. Reads the .md files from repo root. |
| `llm_client.py` | LLM provider abstraction — local llama-server (default) / Anthropic / DashScope cloud Qwen. |
| `pi_client.py` | Async httpx wrapper for every Pi bridge endpoint. |
| `tools.py` | OpenAI tool schemas + dispatch map (`build_dispatch`/`dispatch_tool`) + `capture_vision_tool` + TTS path. Dispatches by string name. |
| `motion_lock.py` | Single-motion-at-a-time guard across move/pose; state observed by events. |
| `loop_helpers.py` | Loop-window mechanics: frame stack, compaction, pacing, tool-call splitting. |
| `scratchpad.py` | Mechanical running-state block rendered into each turn's messages. |
| `session_profiler.py` | Records per-call metrics; `save(out_dir, ...)` writes a session `.md` to `out/`. |
| `habits.py` | Habit-tool bodies; holds the deferred `investigate`/`explore_entry` placeholders (not yet wired). |
| `spells.py` | Spell implementations (wand pose + soundbite + Tuya/HA call). |
| `voice.py` | Wake word (openWakeWord) + Whisper STT; `record_push_to_talk()` one-shot PTT. |
| `gui_server.py` | Browser GUI (FastAPI + SSE): `/events`, `/ptt`, `/handsfree`, `/chat`, `/api/config`. |
| `launcher.py` | Pre-launch config screen (TTY) that runs before env-reading imports. |
| `world.py` | World-map model (nodes/anchors) persisted to `data/world.json`. |

### core/explore/ — exploration subagent (deferred; kept, not wired into brain)

| File | Purpose |
|---|---|
| `agent.py` | `run_explore()` subagent loop; reads `EXPLORE.md` prompt. (was `explore_agent.py`) |
| `tools.py` | Scoped move/capture/commit tools the explore agent calls. (was `explore_tools.py`) |
| `scope.py` | Explore scope/state: node graph, message tagging/splicing, return planning. |

### core/chat/ — standalone text chat CLI

| File | Purpose |
|---|---|
| `chat.py` | Text-only chat loop (`python -m core.chat.chat`). |
| `prompt.py` | Loads `CHAT.md` into the chat system prompt. (was `chat_prompt.py`) |

## pi_bridge/ — Pi-side FastAPI bridge (shipped to the Pi)

| File | Purpose |
|---|---|
| `server.py` | FastAPI bridge: `/move`, `/pose`, `/set_legs`, `/trick`, `/distance`, `/capture`, `/battery`, `/perception`, `/face`, `/health`, `/speak`, `/play_wav`. Started with sudo (GPIO). |
| `sequence.py` | Animation-sequence playback helper used by the bridge. |
| `chotu/face.py` | Pi face-panel rendering. **Do not rename `pi_bridge/chotu/`** — separate Pi runtime package. |
| `test_sequence.py` | Pi-side sequence tests. |

## scripts/ — laptop dev tools (grouped by domain; not part of the brain)

### scripts/animation/
| File | Purpose |
|---|---|
| `animation_studio.py` | Browser pose/animation designer on :8899; proxies motion endpoints to the Pi; serves `studio.html`. |
| `gen_builtin_animations.py` | Generates `assets/Animations/builtin/*.json`. |
| `validate_animation.py` | Validates frames JSON against kinematics reachability; `--install` copies into Animations. |
| `render_animation.py` | Matplotlib contact-sheet renderer + stability overlay for an animation. |
| `kinematics_ref.py` | Leg IK constants + `coord2polar`/`is_reachable` (laptop mirror of the Pi-only lib). |

### scripts/robot/
| File | Purpose |
|---|---|
| `chotu_tool.py` | One-shot tool invoker for the CC `chotu` skill (state in `/tmp`). |
| `chotu_repl.py` | Interactive explore REPL driving scoped tools. |
| `cc_viewer.py` | Viewer for CC-driven sessions. |
| `sim_loop.py` | Simulated brain loop (fake Pi + rotating frames). |
| `dry_run.py` | Scenario dry-runner; writes dialogue/metrics to `out/`. |

### scripts/bench/  (probes + benchmarks — NOT collected by pytest)
| File | Purpose |
|---|---|
| `benchmark_models.py` | Benchmarks brain models; writes a comparison table to `out/`. |
| `measure_fps_budget.py` | Measures per-loop frame/timing budget. |
| `measure_image_tokens.py` | Measures image token cost for vision frames. |
| `probe_cache.py` | Manual cloud-cache probe (was `test_cache.py`). |
| `explore_dry.py` | Manual explore-agent dry run (was `test_explore_dry.py`). |
| `dry_probe.py` | Manual dry-run probe (was `test_dry.py`). |
| `animation_endpoints_probe.py` | Studio endpoint tests — mutates `assets/`, so kept as a probe not a unit test. |
| `gen_builtin_probe.py` | Regenerates+validates builtin animations — mutates `assets/`, kept as a probe. |

### scripts/faces/
| File | Purpose |
|---|---|
| `generate_faces.py` | Generates the face PNG set (`scripts/faces/*.png`). |

## Tests, docs, assets

- `tests/` — pytest suite (`testpaths=tests`); the only auto-collected tests.
- `docs/superpowers/specs|plans/` — design specs + implementation plans (force-added; `docs/` is otherwise gitignored).
- `assets/` — gitignored: `Animations/` (load-bearing JSON), `faces/`, `spells/`, photos, CAD reference.
- `out/` — gitignored run-outputs (session logs, benchmarks, renders).
