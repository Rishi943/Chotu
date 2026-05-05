# Chotu — Context for Resume Writing

## One-line pitch

Chotu is an LLM-driven embodied AI agent running on a SunFounder PiCrawler quadruped robot, with voice input, on-device vision, TTS, and Home Assistant integration.

---

## Project metadata

- **Start date:** 2026-04-21
- **Commit count:** 80
- **Status:** Active (Phase 1 shipped, ongoing)
- **License:** None specified
- **Public URL:** None — local/private project

---

## Problem it solves

Consumer robotics kits provide hardware and pre-baked action libraries but no natural-language interface. Chotu makes a physical robot responsive to spoken or typed instructions, capable of autonomous goal pursuit, and aware of its environment via ultrasonic sensors and a camera — without relying on cloud services for inference.

---

## Architecture overview

The system splits across two machines: a laptop runs the LLM, TTS, STT, and agent loop; a Raspberry Pi 5 runs a thin FastAPI HTTP bridge that wraps the PiCrawler hardware APIs (servos, ultrasonic, camera, OLED). The laptop sends tool-call results as JSON envelopes over LAN via async httpx; the Pi never makes decisions — it only executes commands and returns structured responses.

The agent loop on the laptop is a custom ~30-line asyncio cycle (no LangChain, no framework). It supports two modes: reactive (one user turn → LLM → tool calls → reply) and goal/autonomous (LLM loops with ambient state injection until it calls `goal_complete()`). Parallel tool dispatch uses `asyncio.gather()`; speech fires as a background task concurrently with tool execution. A browser GUI served by FastAPI on port 8888 receives live events via SSE and shows tool calls, spoken text, and camera snapshots in real time.

---

## Tech stack

**Languages:** Python 3.12 (laptop + Pi)

**Frameworks/Libraries:**
- FastAPI + uvicorn (both sides: Pi bridge on port 7000, GUI server on port 8888)
- httpx (async HTTP client, laptop → Pi)
- openai (AsyncOpenAI SDK, used against local llama-server endpoint)
- anthropic (optional fallback provider)
- sounddevice + numpy (audio playback)
- faster-whisper (STT, `small` model, CPU int8)
- openwakeword (ONNX inference, hey_jarvis_v0.1.onnx)
- piper (neural TTS, run as subprocess)
- opencv-python-headless (JPEG encoding on Pi)
- picrawler, robot_hat, vilib (SunFounder HAL, pre-installed on Pi OS image)
- pygame (audio playback on Pi side for /speak endpoint)
- python-dotenv, pytest, pytest-asyncio

**Models/AI:**
- `Qwen3.5-4B-Q4_K_M.gguf` — multimodal (text + vision), run via llama-server (llama.cpp) on laptop GPU
- `mmproj-BF16.gguf` — multimodal projector for vision
- `hey_jarvis_v0.1.onnx` — wake word detection
- faster-whisper small (en, CPU int8) — speech-to-text
- `en_GB-northern_english_male-medium.onnx` — piper TTS voice

**Infra/DevOps:**
- llama-server (llama.cpp) started manually on laptop, port 8080
- Pi runs uvicorn with sudo (GPIO requirement)
- mDNS (`chotu.local`) for Pi discovery; IP fallback in `.env`
- SSH-only Pi access; deploy via scp

**Frontend:** Single-page HTML/JS (served by FastAPI), SSE event stream, MJPEG camera proxy

**Testing/Eval:** `scripts/dry_run.py` — runs real brain loop against llama-server with faked Pi responses; prints tool calls and spoken lines. Manual only; no automated test suite for behavior.

---

## Notable engineering decisions

- **Speech moved from tool call to message content.** An earlier design had `speak()` as a function-call tool. Qwen3.5-4B reliably called physical tools but emitted speech as plain text in `message.content` (5 of 8 dry-run prompts failed). The fix: parse `message.content` in brain.py and fire piper directly, removing the `speak` tool entirely. This matches how small models actually behave — examples in the prompt show only spoken text, never function-call syntax.

- **Vision injected as a deferred user message, not inline with tool results.** Qwen3.5 is multimodal but the llama-server rejects `tool → user (image) → tool` sequences. When `capture_vision` returns, the JPEG is held in a `deferred_vision` list and appended after all tool results for that turn, producing a valid `[tool results] → [image user message]` sequence.

- **TTS lock serializes all audio through one asyncio.Lock.** Concurrent `sd.play()` calls corrupt PortAudio state and segfault. Piper synthesis runs outside the lock (so the next utterance renders in parallel with current playback), but `sd.play()` / `sd.wait()` are always under the lock. Spell soundbites use the same lock.

- **Pose speed hard-capped at 50 on the Pi bridge.** `stand` and `sit` move all 12 servos simultaneously. At speed > 50, current draw spikes and causes brownouts. The cap is enforced server-side (`MAX_POSE_SPEED = 50`) so the LLM cannot request a dangerous speed regardless of what it generates.

- **Obstacle poller drives an asyncio.Event estop, not a flag.** A background task polls the ultrasonic sensor every 200ms. When distance < 15cm, it sets an `asyncio.Event`. The dispatch map checks this event before forwarding `move` or `set_legs` calls to the Pi — blocked calls return a silent fake-success envelope so the LLM doesn't see an error and retry.

- **Provider-agnostic LLM client with OpenAI and Anthropic backends.** `LLMClient` normalizes both providers into the same dataclass response shape. The local llama-server speaks OpenAI-compatible JSON; the Anthropic backend translates tool schemas and consolidates consecutive tool-result messages (which Anthropic requires in a single user turn). brain.py never branches on provider.

- **Goal mode uses context compression to prevent vision bloat.** In long autonomous runs, each `capture_vision` call adds a base64 JPEG (~50KB decoded) to the message history. `_compress_vision_in_history()` replaces all but the most recent image block with a text placeholder, keeping the context window bounded.

- **Tool suppression before Pi dispatch.** Guards (`set_legs` max 12/turn, `wait` max 1/turn, failed-tool blacklist) are checked before any Pi network call. Suppressed calls get a fake-success envelope fed back to the model so the conversation stays valid — the model sees a result for every call it made, preventing re-tries or confusion.

---

## Quantifiable details

- **Tools exposed to LLM:** 12 (move, pose, set_legs, do_trick, get_distance, get_battery, capture_vision, set_face, wait, get_perception, cast_spell, goal_complete)
- **Reaction-mode tool iteration cap:** 6 per user turn
- **Goal-mode iteration caps:** 40 outer × 12 inner iterations
- **set_legs guard:** max 12 frames per turn
- **Obstacle stop threshold:** 15cm (ultrasonic poll every 200ms)
- **Pose speed cap:** 50/100 (server-enforced)
- **TTS sample rate:** 22050 Hz; 100ms silence pad at start to prevent clipping
- **STT model:** faster-whisper small, CPU int8; max recording 10s, silence timeout 1.5s
- **Wake word threshold:** 0.5 (configurable via env)
- **Memory buffer:** deque(maxlen=15) turns, in-process only
- **Battery warning thresholds:** 75%, 50%, 15%
- **Camera JPEG quality:** 60 (capture), 70 (MJPEG stream)
- **GUI event queue:** maxsize 200
- **Gallery store:** max 50 images in-process
- **Commit count:** 80 over ~2 weeks
- **System prompt size:** ~9.3KB (~1450 tokens)
- **LLM model size:** 4B parameters, Q4_K_M quantization

---

## Features shipped vs. planned

**Shipped:**
- Full reactive mode: terminal/voice input → LLM → physical action → TTS response
- Goal/autonomous mode with ambient state injection and `goal_complete` signaling
- Voice input: openWakeWord wake detection + faster-whisper STT
- Local TTS via piper (laptop) with phonetic substitution and silence pad
- Obstacle detection with automatic movement estop
- Per-leg coordinate control (`set_legs`) for custom gaits, chained frame-by-frame
- Vilib computer vision: color detection, face detection, human detection
- OLED face expressions synced to speech and tool state
- Home Assistant light control via spell system (lumos/nox/avada_kedavra) with wand pose + soundbite
- Browser GUI with SSE event stream, MJPEG camera proxy, chat input, mode switching
- Battery monitor with threshold announcements
- Dry-run harness for offline prompt evaluation with fake Pi responses
- Provider-agnostic LLM client (local llama-server + Anthropic API)
- Continuous voice mode (no wake word between turns when conversation is active)

**Planned / not yet usable:**
- "Hey Chotu" custom wake word (current wake word is hey_jarvis placeholder; no recordings collected)
- Persistent memory across restarts (in-process deque only; no jsonl or SQLite)
- Token budget monitoring per turn
- On-Pi verification of full checklist against real hardware post-charging

---

## What this project demonstrates about the builder

- End-to-end ownership of a multi-modal, multi-process AI system: model selection and inference configuration, custom agent loop, hardware abstraction layer, voice pipeline, TTS, and a browser UI — all written without scaffolding frameworks.
- Debugging LLM behavioral failures empirically: identified that a 4B model reliably ignores tool-call formatting for speech despite correct prompt examples, diagnosed via a dry-run harness, and re-architected the speech path around actual model behavior rather than theoretical capability.
- Systems thinking across hardware constraints: designed around brownout risk (servo speed caps), PortAudio segfaults (TTS lock), multimodal message ordering constraints (deferred vision), and context window growth (image compression) — all concrete physical/runtime limits, not hypothetical.
- Ability to build and ship incrementally: 80 commits in ~2 weeks, a working end-to-end demo before adding voice, spells, GUI, or autonomous mode.
- Cross-domain integration: LLM inference (llama.cpp), robotics HAL (PiCrawler/robot_hat), home automation (Home Assistant REST), speech (piper/Whisper/openWakeWord), and a real-time browser UI all wired into one coherent system.

---

## Things to NOT claim

- **No production deployment.** The system runs on a private LAN; it has never been accessed by anyone other than the builder.
- **No real users.** All testing is manual by the developer.
- **No formal eval suite.** The dry-run harness prints tool calls and speech for inspection; there are no automated pass/fail tests for behavior or personality.
- **Wake word is a placeholder.** The "hey chotu" wake word does not exist. The system uses a pre-trained hey_jarvis model.
- **Memory is ephemeral.** Conversation history is lost on every restart; no persistence has been implemented.
- **On-Pi runtime verification is pending.** The checklist of physical robot tests has not been run since the most recent code changes.
- **No multi-user or concurrent session support.** The brain loop processes one turn at a time from a single queue.
- **Latency not measured.** Round-trip time from speech to physical action has not been benchmarked.

---

## Useful snippets for cover letters

**The speech-as-tool failure and fix:**
When building Chotu's voice, I initially implemented `speak()` as a standard function-call tool alongside `move()` and `get_distance()`. In dry-run testing, the 4B Qwen model called physical tools correctly but consistently emitted speech as plain text in its response content rather than as a tool call — five of eight test prompts failed. Rather than fighting the model with prompt engineering, I re-read the model's actual output pattern and redesigned around it: speech is now parsed from `message.content` and piped to the TTS engine directly, with no tool involved. The model does exactly what it naturally does; the architecture adapted to match. This changed a reliability problem into a non-issue.

**Designing around a hardware constraint discovered mid-build:**
During initial testing, the robot would occasionally lose power and reset mid-command. Tracing the issue revealed that `stand` and `sit` activate all 12 servos simultaneously, and at the default speed of 80, the current spike was enough to brownout the 2S LiPo. I added a hard server-side speed cap (`MAX_POSE_SPEED = 50`) enforced in the FastAPI bridge, so the LLM cannot request a dangerous speed regardless of what it generates. The constraint is documented in the tool schema description so the model learns the safe default, and enforced in code as a failsafe.

**Vision ordering in a multimodal tool loop:**
Integrating camera snapshots into the agent loop exposed a constraint in llama-server's multimodal message validation: a `tool_result` message immediately followed by a `user` message containing an image, followed by another `tool_result`, produces an invalid sequence that the server rejects. I traced this by reading the raw HTTP error, then designed a `deferred_vision` list in the dispatch loop: when `capture_vision` returns, the JPEG is held back and appended after all other tool results for that turn. The model always sees images at a valid position in the conversation, without any changes to how it calls the tool.

---

## Open questions (fill in manually)

- Has the full runtime checklist been verified on the physical robot after the most recent code push?
- What is the actual round-trip latency from spoken input to physical robot movement?
- Has the system been demonstrated to anyone other than the developer?
