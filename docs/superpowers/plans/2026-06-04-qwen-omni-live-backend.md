# Qwen-Omni Live Backend — Implementation Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second Backend implementation (`QwenOmniBackend`) targeting Alibaba's `qwen3.5-omni-flash-realtime` model via the DashScope realtime WebSocket API. Plug it into the existing `core/brain_live.py` via a provider switch so the live brain can run on Qwen or Gemini with the same downstream code.

**Architecture:** The `Backend` Protocol from `core/backend.py` already abstracts LLM transport. `QwenOmniBackend` is a sibling to `GeminiLiveBackend` — both implement the same async surface (`start`, `send_user_text`, `send_frame`, `send_tool_result`, `events`, `close`). `brain_live.py` reads `PALIV_LIVE_PROVIDER` and instantiates one. `FrameSampler`, `MotionLock`, persona overlays, and the dispatch map are backend-agnostic.

**Tech Stack:** DashScope Python SDK (`dashscope >= 1.25.17`) for the realtime WS client. The SDK is callback-based and synchronous; a thin adapter bridges its `OmniRealtimeCallback` hooks into our `asyncio.Queue[Event]` stream.

## Decisions (locked from spec session 2026-06-04)

| Question | Decision |
|---|---|
| Model | `qwen3.5-omni-flash-realtime` (user confirmed from Alibaba console) |
| Client | DashScope SDK (`OmniRealtimeConversation`) — official path, auth + protocol handled |
| Auth env var | `DASHSCOPE_API_KEY` |
| WS URL | `wss://ws-co0vxhxnl0xu0007.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/realtime` (user's workspace) |
| Output modality | Text only — Chotu speaks via Piper TTS on the laptop; saves audio tokens |
| `smooth_output` | `None` (auto) — model picks conversational vs written |
| `turn_detection` | Manual mode (disabled) — we control turn boundaries explicitly to mirror Gemini flow |
| Image input | Yes, via `append_video()` at 1 FPS (driven by existing FrameSampler) |
| Audio input | None — laptop is text-driven, no mic into realtime API |
| Tool calling | Trust the docs say tools work on the Qwen3.5-Omni-Realtime family. Verified during E2E (Task 5) |

## Known risks

1. **Tool calling on the flash variant.** Public docs say `tools` "takes effect only when you use the Qwen3.5-Omni-Realtime model." Whether `qwen3.5-omni-flash-realtime` (flash subvariant) inherits this is unverified. If E2E (Task 5) shows the model never emits `response.function_call_arguments.done`, the fallback is to either (a) switch to `qwen3.5-omni-realtime` proper or (b) re-spec a JSON-in-monologue tool protocol. Document the finding before any fix.
2. **Audio timeline alignment.** Docs say "audio stream is the input timeline; images are inserted into the audio stream by send time." With no audio input, image insertion behavior is undocumented. Expectation: frames still land in context for the next response. If E2E shows the model never references frames, send 100 ms of silence as filler.
3. **Manual mode + response.create cadence.** In manual mode every response must be explicitly requested via `create_response`. We invoke it after each user-text send and after each tool-result send. Missing a call = silent hang.
4. **Session TTL.** Gemini Live caps sessions ~10 min. Qwen-Omni's limit is undocumented. v1 policy is fail-loud: on WS close emit `SessionEnded` and let the brain stop. Reconnect is v2.
5. **Callback threading.** DashScope SDK callbacks run on the SDK's WS thread, not the asyncio loop. The bridge MUST use `loop.call_soon_threadsafe` (or `asyncio.run_coroutine_threadsafe`) to push events into the queue. Direct `queue.put_nowait` from the SDK thread is a race waiting to happen.

## File structure

| File | Status | Responsibility |
|---|---|---|
| `core/qwen_omni_backend.py` | NEW | DashScope SDK adapter implementing the Backend Protocol |
| `core/brain_live.py` | MODIFY | Add `PALIV_LIVE_PROVIDER` switch (qwen \| gemini) |
| `.env.example` | MODIFY | Add `DASHSCOPE_API_KEY`, `PALIV_QWEN_OMNI_WS_URL`, `PALIV_QWEN_OMNI_MODEL`, `PALIV_LIVE_PROVIDER` |
| `requirements.txt` | MODIFY | Add `dashscope>=1.25.17` |
| `tests/test_qwen_callback_bridge.py` | NEW | Unit test for the callback→asyncio queue bridge using a fake callback driver |

---

## Task 1: Dependencies and env vars

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`

- [ ] **Step 1: Add SDK to requirements.txt**

Append:
```
dashscope>=1.25.17
```

- [ ] **Step 2: Install**

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c "from dashscope.audio.qwen_omni import OmniRealtimeConversation, OmniRealtimeCallback, MultiModality, AudioFormat; print('sdk ok')"
```

Expected: prints `sdk ok`.

- [ ] **Step 3: Extend .env.example**

Append:
```
# Live brain — Qwen-Omni Realtime (default backend)
DASHSCOPE_API_KEY=
PALIV_QWEN_OMNI_WS_URL=wss://ws-co0vxhxnl0xu0007.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/realtime
PALIV_QWEN_OMNI_MODEL=qwen3.5-omni-flash-realtime
PALIV_LIVE_PROVIDER=qwen        # qwen | gemini
```

- [ ] **Step 4: Commit**

```bash
git add requirements.txt .env.example
git commit -m "deps: add dashscope SDK for Qwen-Omni Realtime backend"
```

---

## Task 2: Callback→asyncio bridge with unit test

**Files:**
- Create: `tests/test_qwen_callback_bridge.py`
- Create: `core/qwen_omni_backend.py` (just the bridge class for this task)

The DashScope SDK delivers events via a synchronous `OmniRealtimeCallback.on_event(msg)` method called on the SDK's WS thread. We need those events on the asyncio loop where the brain consumer reads them. The bridge owns:

- A reference to the asyncio loop
- An `asyncio.Queue` of decoded `Event` instances
- Thread-safe `put` via `loop.call_soon_threadsafe`
- A simple `on_event` that classifies the raw dict, builds the right `Event`, and schedules the put

- [ ] **Step 1: Write the failing test**

```python
"""Bridge test: feed synthetic DashScope events from a non-loop thread,
confirm they arrive on the asyncio queue in order."""

import asyncio
import threading

from core.qwen_omni_backend import _QwenEventBridge
from core.backend import AssistantText, ToolCall, SessionEnded


async def test_text_done_emits_assistant_text():
    loop = asyncio.get_running_loop()
    bridge = _QwenEventBridge(loop)
    # Simulate two deltas + done
    bridge.on_event({"type": "response.text.delta", "delta": "Hello, "})
    bridge.on_event({"type": "response.text.delta", "delta": "world."})
    bridge.on_event({"type": "response.text.done", "text": "Hello, world."})

    ev = await asyncio.wait_for(bridge.queue.get(), timeout=1.0)
    assert isinstance(ev, AssistantText)
    assert ev.text == "Hello, world."


async def test_function_call_done_emits_toolcall():
    loop = asyncio.get_running_loop()
    bridge = _QwenEventBridge(loop)
    bridge.on_event({
        "type": "response.function_call_arguments.done",
        "call_id": "call-1",
        "name": "move",
        "arguments": '{"direction": "forward", "steps": 2}',
    })

    ev = await asyncio.wait_for(bridge.queue.get(), timeout=1.0)
    assert isinstance(ev, ToolCall)
    assert ev.id == "call-1"
    assert ev.name == "move"
    assert ev.args == {"direction": "forward", "steps": 2}


async def test_threadsafe_put_from_other_thread():
    """The real SDK runs callbacks on its WS thread. Verify the bridge
    routes events from a non-loop thread without deadlock or loss."""
    loop = asyncio.get_running_loop()
    bridge = _QwenEventBridge(loop)

    def producer():
        for i in range(5):
            bridge.on_event({"type": "response.text.done", "text": f"msg{i}"})

    t = threading.Thread(target=producer)
    t.start()
    t.join()

    received = []
    for _ in range(5):
        ev = await asyncio.wait_for(bridge.queue.get(), timeout=1.0)
        received.append(ev.text)
    assert received == [f"msg{i}" for i in range(5)]


async def test_close_emits_session_ended():
    loop = asyncio.get_running_loop()
    bridge = _QwenEventBridge(loop)
    bridge.on_close(1000, "normal closure")
    ev = await asyncio.wait_for(bridge.queue.get(), timeout=1.0)
    assert isinstance(ev, SessionEnded)
    assert "1000" in ev.reason or "normal" in ev.reason
```

- [ ] **Step 2: Verify failing**

```bash
.venv/bin/python -m pytest tests/test_qwen_callback_bridge.py -v
```

Expected: ImportError on `_QwenEventBridge`.

- [ ] **Step 3: Implement `_QwenEventBridge` in `core/qwen_omni_backend.py`**

The bridge:
- Holds `self.loop` and `self.queue: asyncio.Queue[Event]`
- `on_event(msg: dict)` decodes by `msg["type"]` and calls `_emit(event)`
- `on_close(code, reason)` calls `_emit(SessionEnded(...))`
- `on_error(err)` calls `_emit(BackendError(message=str(err), recoverable=False))`
- `_emit(event)` uses `self.loop.call_soon_threadsafe(self.queue.put_nowait, event)`

Event mapping:
- `response.text.delta` → accumulate into a `self._partial_text` buffer
- `response.text.done` → if event carries full `text`, emit that; else emit accumulated buffer; clear buffer
- `response.audio_transcript.delta` / `.done` → same buffer logic (audio transcript is the text when audio modality is on; with text-only we shouldn't see these but handle gracefully)
- `response.function_call_arguments.delta` → accumulate per `call_id` into a `self._partial_calls: dict[str, dict]` keyed by call_id (each entry holds `name`, `arg_buf`)
- `response.function_call_arguments.done` → emit `ToolCall(id=call_id, name=name, args=json.loads(arguments or buffered))`; remove from partial dict
- `response.done` → no-op (turn boundary; we don't surface it as an Event in v1)
- `error` → emit `BackendError`
- Anything unknown → debug log + ignore

- [ ] **Step 4: Verify green**

```bash
.venv/bin/python -m pytest tests/test_qwen_callback_bridge.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add core/qwen_omni_backend.py tests/test_qwen_callback_bridge.py
git commit -m "feat(qwen): event-bridge from DashScope SDK callbacks to asyncio queue"
```

---

## Task 3: QwenOmniBackend — full Backend implementation

**Files:**
- Modify: `core/qwen_omni_backend.py`

Implements the Backend protocol around `OmniRealtimeConversation`. The SDK is synchronous; backend methods wrap blocking calls with `loop.run_in_executor` to keep the asyncio loop free.

- [ ] **Step 1: Extend `core/qwen_omni_backend.py` with `QwenOmniBackend` class**

Constructor:
```python
def __init__(
    self,
    *,
    system_prompt: str,
    tool_schemas: list[dict],
    model: str | None = None,
    api_key: str | None = None,
    ws_url: str | None = None,
) -> None:
```

Reads env defaults: `PALIV_QWEN_OMNI_MODEL`, `DASHSCOPE_API_KEY`, `PALIV_QWEN_OMNI_WS_URL`. Raises `ValueError` if key not set.

- [ ] **Step 2: Implement `start()`**

- Capture `self._loop = asyncio.get_running_loop()`
- Build the bridge: `self._bridge = _QwenEventBridge(self._loop)`
- Construct `OmniRealtimeConversation(model=..., callback=self._bridge, url=ws_url)`
- Set `dashscope.api_key = self._api_key`
- `await loop.run_in_executor(None, conv.connect)` — blocking connect off-loop
- `await loop.run_in_executor(None, conv.update_session, ...)` with:
  - `output_modalities=[MultiModality.TEXT]`
  - `voice=None` (text only)
  - `enable_turn_detection=False`
  - `instructions=system_prompt`
  - `tools=tool_schemas` (passed directly — OpenAI function schema, matches Qwen's expected shape per docs)
- Store `self._conv = conv`

- [ ] **Step 3: Implement `send_user_text(text)`**

```python
async def send_user_text(self, text: str) -> None:
    item = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text}],
    }
    await self._loop.run_in_executor(None, self._conv.create_item, item)
    await self._loop.run_in_executor(None, self._conv.create_response)
```

(Two off-loop calls: create the conversation item, then request a response since we're in manual mode.)

- [ ] **Step 4: Implement `send_frame(jpeg_bytes, ts)`**

```python
async def send_frame(self, jpeg_bytes: bytes, ts: float) -> None:
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    await self._loop.run_in_executor(None, self._conv.append_video, b64)
```

No `create_response` call here — frames are inserted into context but don't trigger a turn on their own. The model only responds when a user text or tool result arrives.

- [ ] **Step 5: Implement `send_tool_result(tool_call_id, result)`**

```python
async def send_tool_result(self, tool_call_id: str, result: dict) -> None:
    item = {
        "type": "function_call_output",
        "call_id": tool_call_id,
        "output": json.dumps(result),
    }
    await self._loop.run_in_executor(None, self._conv.create_item, item)
    await self._loop.run_in_executor(None, self._conv.create_response)
```

- [ ] **Step 6: Implement `events()` and `close()`**

`events()` drains `self._bridge.queue`:
```python
async def events(self) -> AsyncIterator[Event]:
    while not self._closed.is_set() or not self._bridge.queue.empty():
        try:
            ev = await asyncio.wait_for(self._bridge.queue.get(), timeout=0.5)
            yield ev
        except asyncio.TimeoutError:
            continue
```

`close()`:
```python
async def close(self) -> None:
    self._closed.set()
    if self._conv:
        try:
            await self._loop.run_in_executor(None, self._conv.close)
        except Exception as e:
            log.warning("Qwen close error: %s", e)
```

- [ ] **Step 7: Smoke-import**

```bash
.venv/bin/python -c "from core.qwen_omni_backend import QwenOmniBackend; print('import ok')"
```

Expected: `import ok` (no actual connect — needs API key).

- [ ] **Step 8: Commit**

```bash
git add core/qwen_omni_backend.py
git commit -m "feat(qwen): QwenOmniBackend implementing Backend protocol via DashScope SDK"
```

---

## Task 4: Provider switch in brain_live.py

**Files:**
- Modify: `core/brain_live.py`

- [ ] **Step 1: Replace the hardcoded `GeminiLiveBackend` construction with a switch**

In `core/brain_live.py main()`, find:
```python
backend = GeminiLiveBackend(system_prompt=system_prompt, tool_schemas=TOOL_SCHEMAS)
```

Replace with:
```python
provider = os.getenv("PALIV_LIVE_PROVIDER", "qwen").lower()
if provider == "qwen":
    from core.qwen_omni_backend import QwenOmniBackend
    backend = QwenOmniBackend(system_prompt=system_prompt, tool_schemas=TOOL_SCHEMAS)
    backend_name = os.getenv("PALIV_QWEN_OMNI_MODEL", "qwen3.5-omni-flash-realtime")
elif provider == "gemini":
    from core.gemini_live_backend import GeminiLiveBackend
    backend = GeminiLiveBackend(system_prompt=system_prompt, tool_schemas=TOOL_SCHEMAS)
    backend_name = os.getenv("PALIV_GEMINI_MODEL", "gemini-3.1-flash-live-preview")
else:
    raise SystemExit(f"PALIV_LIVE_PROVIDER must be 'qwen' or 'gemini', got {provider!r}")

print(f"  provider: {provider} ({backend_name})")
```

Also remove the unconditional `from core.gemini_live_backend import GeminiLiveBackend` at module top — both backends are imported lazily inside the switch so the unused one's dependency (google-genai or dashscope) doesn't have to be installed.

- [ ] **Step 2: Replace the existing `model:` log line**

The existing print already shows model — keep it consistent. Delete or update:
```python
print(f"  model: {os.getenv('PALIV_GEMINI_MODEL', 'gemini-3.1-flash-live-preview')}")
```
to use the `backend_name` from the switch.

- [ ] **Step 3: Smoke-import both paths**

```bash
PALIV_LIVE_PROVIDER=qwen   .venv/bin/python -c "import core.brain_live; print('qwen import ok')"
PALIV_LIVE_PROVIDER=gemini .venv/bin/python -c "import core.brain_live; print('gemini import ok')"
```

Expected: both print `ok`.

- [ ] **Step 4: Commit**

```bash
git add core/brain_live.py
git commit -m "feat(brain_live): PALIV_LIVE_PROVIDER switch (qwen | gemini)"
```

---

## Task 5: End-to-end live verification

**Files:** None (verification only)

This task does not change code. It runs the live brain against the real Pi and real Qwen-Omni session.

- [ ] **Step 1: Prepare environment**

```bash
source .venv/bin/activate
export DASHSCOPE_API_KEY=<your-key>
export PALIV_LIVE_PROVIDER=qwen
export PALIV_MUTE=1   # skip TTS during first run
```

Confirm the Pi bridge is up and `/stream` is reachable:
```bash
curl -4 -s --max-time 2 http://192.168.0.190:7000/health
curl -4 -s --max-time 3 http://192.168.0.190:7000/stream | head -c 100 | xxd | head
```

Expected: `/health` `ok: true`; `/stream` shows `--frame\r\nContent-Type: image/jpeg\r\nContent-Length: N\r\n\r\n...`.

- [ ] **Step 2: Start the brain**

```bash
python -m core.brain_live
```

Watch for:
- `provider: qwen (qwen3.5-omni-flash-realtime)`
- `Pi bridge: connected`
- Wake nudge sent (`[system] You are awake. Live your life.`)
- Within ~5 s: a `chotu>` line OR a `[tool]` line — model reacted to the wake nudge or the first frames.

If nothing appears within 30 s, dump the bridge state: `Ctrl+C`, set `PALIV_DEBUG=1`, re-run, inspect events.

- [ ] **Step 3: Verify tool calling actually works on this model variant**

This is the critical risk check from the "Known risks" section. At the implicit input prompt, type:
```
walk forward two steps
```

Expected: `[tool] move({"direction": "forward", "steps": 2})` line appears, followed by `-> ok` (or `-> err: ...` if Pi rejects). If no tool call ever shows up — model only emits text — tools are NOT supported on `qwen3.5-omni-flash-realtime`. STOP and document in this file under a new "## Phase 1 results" section. Choose between switching model id to `qwen3.5-omni-realtime` (non-flash) and the JSON-in-monologue fallback.

- [ ] **Step 4: Verify motion lock enforcement**

Type:
```
do a pushup, and also walk forward four steps
```

Watch for:
- A `do_trick(pushup)` tool call.
- A `move(...)` tool call attempted while the trick is in flight.
- The `move` result is the rejection envelope: `motion in progress: do_trick(pushup), ~Xs remaining`.
- The model does NOT immediately retry. If it does, persona file `CHOTU_LIVE.md` motion-lock section needs strengthening.

- [ ] **Step 5: Verify obstacle reactivity**

While Chotu is mid-walk, place an object 20 cm in front. Expect within ~2 s an `AssistantText` or `speak` tool call referencing the obstacle BEFORE the current move ends. If it never does, frames may not be making it into context — confirm with `PALIV_DEBUG=1` that `append_video` is being called.

- [ ] **Step 6: Verify clean disconnect**

`Ctrl+C`. Expect:
- `[live] shutting down...`
- Tasks cancelled, sampler stopped, backend closed.
- Chotu sits.
- `bye.` printed, process exits.

If anything hangs, check that `close()` actually returns and `pi.pose("sit")` has its timeout enforced.

- [ ] **Step 7: Document findings**

Append a `## Phase 1 results` section to this spec file capturing:
- Whether tools fired (CRITICAL)
- Frame insertion: did the model reference what it saw?
- Motion-lock behavior: did the model retry rejections?
- Disconnect cleanliness
- Latency observations (wake nudge → first reply, frame → reaction)
- Any persona tweaks needed for `CHOTU_LIVE.md`

```bash
git add docs/superpowers/plans/2026-06-04-qwen-omni-live-backend.md
git commit -m "docs(spec): Qwen-Omni live backend Phase 1 results"
```

---

## Definition of Done

- [ ] `dashscope` SDK installed; smoke import works.
- [ ] `_QwenEventBridge` unit tests pass (4/4).
- [ ] `core/qwen_omni_backend.py` defines `QwenOmniBackend` implementing the full Backend protocol (start/send_user_text/send_frame/send_tool_result/events/close).
- [ ] `core/brain_live.py` switches on `PALIV_LIVE_PROVIDER`; both `qwen` and `gemini` imports succeed.
- [ ] `core/brain_live.py` starts with `PALIV_LIVE_PROVIDER=qwen`, connects to Qwen-Omni, dispatches at least one tool call from a typed user prompt.
- [ ] Frames flow at 1 FPS via the existing `FrameSampler` (visible via `PALIV_DEBUG=1`).
- [ ] Motion lock rejects overlapping motion; model does not retry.
- [ ] Clean Ctrl+C shutdown.
- [ ] Phase 1 results appendix written to this spec.

## Phase 1 results (2026-06-04)

End-to-end run against the Pi at `192.168.0.190:7000` with `DASHSCOPE_API_KEY` set, `PALIV_LIVE_PROVIDER=qwen`, model `qwen3.5-omni-flash-realtime`.

**Spec corrections required at runtime — all now reflected in code:**

1. **Wrong WS URL.** The workspace URL in the spec (`wss://ws-co0vxhxnl0xu0007.ap-southeast-1.maas.aliyuncs.com/api-ws/v1/realtime`) accepts the handshake and emits `session.created`, but the gateway then closes with `"Service route not found."` — the model isn't deployed at that route. **Correct URL: `wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime`** (International DashScope, Singapore region — matches the `sk-ws-*` key format). The main-China URL `dashscope.aliyuncs.com` returns 401 InvalidApiKey for this key.
2. **`voice=None` rejected.** Server returns `Voice 'null' is not supported.` even with `output_modalities=[TEXT]`. SDK always serializes the field, so we now pass `voice="Tina"` (default; env-overridable via `PALIV_QWEN_OMNI_VOICE`). Audio output bytes (`response.audio.delta`) are ignored by the bridge; we consume `response.audio_transcript.delta/done` as text.
3. **Risk #2 confirmed — image-only doesn't work.** Server rejects every `append_video` with `Error append image before append audio` unless an audio stream precedes it on the timeline. Single-shot silence-anchor on `start()` is **not enough**: the buffer falls behind real time and the error returns after a few frames. **Implemented fix:** background `_stream_silence()` task pushes 100 ms of PCM16 zero-audio every 100 ms for the life of the session. Wastes a small amount of upload bandwidth; eliminates the error. v1.5 alternative is feeding the laptop mic.
4. **"Conversation already has an active response" race.** Model regularly emits multiple `function_call_arguments.done` events inside a single response. The brain dispatches them serially and `send_tool_result` calls `create_response` after each — the second collides with the response started by the first. **Implemented fixes:** (a) bridge now buffers tool calls until `response.done` and emits them as a batch; (b) the specific error message `"active response"` is treated as recoverable in the bridge (log + continue) since the model already has the tool output via `create_item`. Both together stop the crash; the model continues the active response.
5. **Event schema.** Server emits `response.audio_transcript.delta/done` (not `response.text.delta/done`) because default modality is text+audio with a voice. Bridge handles both.

**Definition-of-Done outcomes:**

- ✅ Connect + session.created against `dashscope-intl.aliyuncs.com`.
- ✅ Tool calls dispatched from typed prompts. Verified `move({direction: forward, steps: 2})` from "walk forward two steps".
- ✅ Tools work on `qwen3.5-omni-flash-realtime` (Risk #1 resolved — no need to fall back to non-flash or JSON-in-monologue).
- ✅ Frames flow at 1 FPS once the silence pump is in place. Model references what it sees (commented on lighting changes from a vision capture).
- ✅ Clean kill: `pkill` of brain process; manual `pose("sit")` over HTTP restores Chotu.
- ⚠️ **Motion-lock contention test not completed.** Cut short due to unrelated model misbehavior (over-eager tool calling, see Persona findings below). Backend-level overlap rejection path is unchanged and still proven via existing `MotionLock` unit tests.
- ⚠️ **Frame-reactivity test not completed.** Same reason. Frames are confirmed flowing and being referenced; obstacle-reaction timing not measured.

**Persona findings (out of scope for this plan — to address in `CHOTU_LIVE.md`):**

- `qwen3.5-omni-flash-realtime` is more impulsive with tools than Gemini Live. On the user prompt "walk forward two steps" it correctly emitted `move` and then unprompted emitted **5 consecutive `cast_spell(lumos/nox/lumos/nox/lumos)`** calls, flashing the user's room lights repeatedly. User had to kill the physical switch to stop the chaos.
- The model also produced a single combined `content: ... speak: ...` AssistantText rather than separating monologue from a `speak` tool invocation — persona prompt may need to specify the speak-tool contract more explicitly for this model.
- Recommended follow-ups: (a) tighten the "only invoke tools the user asked for" rule in `CHOTU_LIVE.md`; (b) consider not exposing `cast_spell` to the live brain by default, or gating it behind a wand-pose precondition; (c) restate the speak-as-tool contract for non-Gemini backends.

**Code deltas vs. spec (committed):**

- `core/qwen_omni_backend.py`: added `_stream_silence()` background task, per-frame audio pairing removed in favor of continuous pump; `voice="Tina"` default with env override; bridge buffers tool calls until `response.done`; recoverable handling of `"active response"` server errors.
- `tests/test_qwen_callback_bridge.py`: tool-call test now exercises the buffer-until-`response.done` contract.
- `.env`: `PALIV_QWEN_OMNI_WS_URL` updated to the intl endpoint.

## Out of scope (v2 / later)

- Audio modality (Qwen voice). Replaces Piper TTS; deferred until v1 stable.
- Mic input via Qwen ASR. Voice input still routed through laptop wake-word + Whisper.
- Auto-reconnect on session close. v1 stops the brain on `SessionEnded`.
- `enable_search` / web search tool. Incompatible with `tools` per docs.
- Multi-user / multi-session.
