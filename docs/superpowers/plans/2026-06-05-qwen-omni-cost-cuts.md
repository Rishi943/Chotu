# Qwen-Omni Live Backend — Cost Reduction

> **Context:** First end-to-end live session (2026-06-04) burned **425,088 tokens** in ~10 minutes of debugging across 5–6 brain restarts. Root causes are the 10 Hz silence pump, 1 FPS uncompressed-ish JPEGs, and possibly unwanted audio output. This plan trims each.
>
> **Goal:** Same live-brain functionality at ~5–10× lower token cost. No new features.

## Baseline (from today's session)

| Source | Estimate | Why it's expensive |
|---|---|---|
| Silence audio input (100 ms every 100 ms) | ~30–60k tokens × N responses ≈ 150k–250k | Continuous PCM16 stream, re-billed in every response's context |
| Video frames (1 FPS @ Pi-default JPEG size) | ~500 tokens/frame × 600 frames ≈ 300k | Each response re-bills all in-context frames |
| Possible audio output | unknown | `output_modalities=[TEXT]` may have been silently overridden — `response.audio.delta` events observed in probes |

Server doesn't cache the input timeline between responses on Qwen-Omni Realtime, so every Nth response pays for the first (N-1) responses' frames + audio again. Tokens grow super-linearly with session length.

## Task 1: Confirm what's actually being billed

**Files:** none — diagnostic only.

The cost story above is hypothesis. Before making changes, measure.

- [ ] Run a short controlled session (~60 s, no debug restarts):
  ```bash
  PALIV_MUTE=1 .venv/bin/python -u -m core.brain_live
  ```
  Send one prompt, let it run for 1 minute, Ctrl+C.

- [ ] Note the per-session token total from the Alibaba console. Divide by 60 to get tokens/sec. This is the baseline.

- [ ] In `core/qwen_omni_backend.py::_QwenEventBridge.on_event`, temporarily add a counter that logs every event type seen and a count when close() is called. Run a 60 s session and confirm whether `response.audio.delta` events are arriving despite `output_modalities=[TEXT]`. If yes → Task 4 is required; if no → Task 4 can be skipped.

---

## Task 2: Drop frame sample rate to 0.25 FPS

**Files:**
- Modify: `core/brain_live.py` (one literal)

- [ ] In `core/brain_live.py`, find:
  ```python
  sampler = FrameSampler(backend=backend, stream_url=stream_url, buffer_size=3, sample_hz=1.0)
  ```
  Change `sample_hz=1.0` to `sample_hz=0.25` (one frame every 4 s).

- [ ] Make it env-overridable for tuning:
  ```python
  sample_hz = float(os.getenv("PALIV_LIVE_FRAME_HZ", "0.25"))
  sampler = FrameSampler(backend=backend, stream_url=stream_url, buffer_size=3, sample_hz=sample_hz)
  ```

- [ ] Verify obstacle reactivity still works. 4 s lag is borderline — Chotu may walk into something it would have seen sooner. If unacceptable in testing, bump to 0.5 FPS. Document the choice you make.

- [ ] Commit: `feat(brain_live): default frame rate to 0.25 FPS, env-tunable via PALIV_LIVE_FRAME_HZ`

---

## Task 3: Shrink JPEG payload Pi-side

**Files:**
- Modify: `pi_bridge/server.py`

The Pi currently encodes at `cv2.IMWRITE_JPEG_QUALITY, 80` at native camera resolution. Image tokens scale with pixel count, not file size — but resolution is what the model sees.

- [ ] In `pi_bridge/server.py::_grab_loop`, downscale before encoding. Add `import cv2` if needed, then before the `imencode` call:
  ```python
  small = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)
  ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
  ```

- [ ] Also bump quality down (`80 → 70`). Marginal token savings, real bandwidth savings.

- [ ] Deploy to Pi:
  ```bash
  scp pi_bridge/server.py chotu@chotu.local:~/chotu-bridge/server.py
  ssh chotu@chotu.local 'sudo systemctl restart chotu-bridge'   # or manual restart
  ```

- [ ] Verify `/capture` still returns a usable image (`curl ... | jq '.result.image_base64 | length'` should show ~10–25 KB base64).

- [ ] Commit: `feat(pi_bridge): downscale stream frames to 640x360, jpeg quality 70`

---

## Task 4: Throttle the silence pump (or remove output audio)

**Files:**
- Modify: `core/qwen_omni_backend.py`

Two independent issues — fix whichever Task 1 confirmed.

**4a. If Task 1 showed `response.audio.delta` events arriving:**

The server isn't honoring our `output_modalities=[TEXT]`. Investigate `update_session` payload. Try:
- [ ] Pass `voice=None` again now that we know the WS URL works (the earlier "Voice 'null'" error may have been workspace-URL specific).
- [ ] If voice=null still rejected, pass `output_modalities=[]` (empty list) and see if the server treats that as no audio.
- [ ] If neither works, the model variant doesn't support text-only output — note in spec and accept the audio cost OR fall back to `qwen-omni-turbo-realtime`.

**4b. Silence pump throttling:**

100 ms every 100 ms is continuous audio. We only need enough audio to keep the timeline ahead of frames. At 0.25 FPS (1 frame every 4 s), we only need silence going in once every ~4 s.

- [ ] In `_stream_silence()`, change the sleep:
  ```python
  await asyncio.sleep(0.1)   # was 100 ms
  ```
  to:
  ```python
  await asyncio.sleep(1.0)   # 100 ms of audio every 1 s = 10% duty cycle
  ```
  This is **10× less audio** with the same timeline coverage as long as the silence chunk size keeps up with frame rate. Watch for the `"append image before append audio"` error returning — if it does, dial back to 0.5 s.

- [ ] Make the interval env-tunable: `PALIV_QWEN_SILENCE_INTERVAL_S` (default 1.0).

- [ ] Commit: `perf(qwen): cut silence pump to 10% duty cycle; gate output-audio fix on Task 1 finding`

---

## Task 5: Measure the win

**Files:** none — verification only.

- [ ] Re-run the same 60 s session as Task 1.
- [ ] Compare tokens/sec to baseline. Target: ≥5× reduction.
- [ ] Document the result in this file under `## Phase 2 results`.

```bash
git add docs/superpowers/plans/2026-06-05-qwen-omni-cost-cuts.md
git commit -m "docs(spec): Qwen-Omni cost-cut Phase 2 results"
```

---

## Definition of Done

- [ ] Task 1 measurements recorded.
- [ ] Frame rate default at 0.25 FPS (or whatever Task 2 verification settled on), env-tunable.
- [ ] Stream frames downscaled to 640×360 @ JPEG q=70 on Pi.
- [ ] Silence pump throttled (or removed if audio-out fix landed).
- [ ] Phase 2 measurement shows ≥5× token reduction at idle.
- [ ] No new feature regressions: motion tools fire, vision still useful, obstacle reactivity acceptable at the chosen frame rate.

## Out of scope (v3)

- Trigger-based frames (only on motion or perception change). Bigger refactor; revisit if 0.25 FPS isn't aggressive enough.
- Real mic input replacing the silence pump. Different design tradeoff (privacy, semantic context) — separate plan.
- Frame-batching (send N frames per response instead of streaming). Doesn't match the live model.
- Switching to non-realtime DashScope chat completions (qwen-vl-max). Loses tool streaming.
