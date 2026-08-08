// Chotu console -- translator panel (left) + camera with one telemetry overlay
// (right). Numbered lanes 1/2 select the SOURCE language; Z / Space (or the talk
// disc) is push-to-talk. The mic capture and teardown paths are ported from the
// Gemma Translator: every stream track is stopped on every exit path so the
// browser recording indicator always goes out.

const $ = (id) => document.getElementById(id);

const talk = $("talk");
const resting = $("resting");
const active = $("active");
const srcText = $("src-text");
const dstText = $("dst-text");
const srcLabel = $("src-label");
const dstLabel = $("dst-label");
const timing = $("timing");
const pane = $("lane-pane");

// "Has been used" -- persisted across reloads like the language choice. Used to
// hide the first-use sphere affordance once a real session has happened.
const USED_KEY = "chotu.used";
let used = false;
try { used = localStorage.getItem(USED_KEY) === "1"; } catch { /* default false */ }
if (used) pane.classList.add("used");
function markUsed() {
  if (used) return;
  used = true;
  try { localStorage.setItem(USED_KEY, "1"); } catch { /* non-persistent */ }
  pane.classList.add("used");
}

// --- numbered language lanes: select the SOURCE language ---

// One shared language list; each lane holds an index into it (like the Gemma
// Translator). Pressing 1 or 2 picks the ACTIVE lane, whose code is what gets
// POSTed as the existing `source` field (unchanged contract). ArrowLeft /
// ArrowRight rotate the active lane through the revolver, skipping the slot the
// other lane holds so the two lanes never show the same language.
const LANGUAGES = [
  { code: "ar", name: "Arabic" },
  { code: "en", name: "English" },
  { code: "es", name: "Spanish" },
  { code: "hi", name: "Hindi" },
  { code: "ja", name: "Japanese" },
  { code: "mr", name: "Marathi" },
  { code: "zh", name: "Chinese" },
  { code: "ko", name: "Korean" },
];
const SOURCE_KEY = "chotu.sourceLang"; // legacy single-language key (migrated on load)
const LANES_KEY = "chotu.lanes"; // activeLane + lang1Index + lang2Index

let lang1Index = 5; // Marathi (today's startup lane 1)
let lang2Index = 1; // English (today's startup lane 2)
let activeLane = 1;
let currentSource = LANGUAGES[lang1Index].code;

function laneIndex(lane) {
  return lane === 1 ? lang1Index : lang2Index;
}

function setLaneIndex(lane, idx) {
  if (lane === 1) lang1Index = idx;
  else lang2Index = idx;
}

// Repaint both lanes and the POSTed source from the current state.
function renderLanes() {
  [1, 2].forEach((num) => {
    const el = document.querySelector('#lanes .lane[data-num="' + num + '"]');
    if (!el) return;
    const lang = LANGUAGES[laneIndex(num)];
    el.dataset.code = lang.code;
    const nameEl = el.querySelector(".lane-name");
    if (nameEl) nameEl.textContent = lang.name.toUpperCase();
    el.classList.toggle("selected", activeLane === num);
  });
  currentSource = LANGUAGES[laneIndex(activeLane)].code;
}

function persistState() {
  try {
    localStorage.setItem(LANES_KEY, JSON.stringify({ activeLane, lang1Index, lang2Index }));
  } catch { /* localStorage unavailable -- selection just does not persist */ }
}

// Pick a lane as active (and as the POSTed source language).
function selectLane(lane) {
  activeLane = lane;
  renderLanes();
  persistState();
}

// Rotate the active lane's language by direction (-1 / +1), skipping the slot
// the other lane holds, wrapping around either way.
function rotateActiveLane(direction) {
  const other = activeLane === 1 ? 2 : 1;
  const N = LANGUAGES.length;
  let ni = (laneIndex(activeLane) + direction + N) % N;
  if (ni === laneIndex(other)) ni = (ni + direction + N) % N;
  setLaneIndex(activeLane, ni);
  renderLanes();
  persistState();
}

function initLanes() {
  // Migrate the legacy single-language key: if it holds a valid code, start
  // lane 1 on it. The newer LANES_KEY (if present) then overrides fully.
  let savedSource = null;
  try {
    savedSource = localStorage.getItem(SOURCE_KEY);
  } catch { /* use default */ }
  const srcIdx = LANGUAGES.findIndex((l) => l.code === savedSource);
  if (srcIdx >= 0) lang1Index = srcIdx;

  let saved = null;
  try {
    saved = localStorage.getItem(LANES_KEY);
  } catch { /* use default */ }
  if (saved) {
    try {
      const st = JSON.parse(saved);
      if (st && (st.activeLane === 1 || st.activeLane === 2)) activeLane = st.activeLane;
      if (st && typeof st.lang1Index === "number" && st.lang1Index >= 0 && st.lang1Index < LANGUAGES.length) lang1Index = st.lang1Index;
      if (st && typeof st.lang2Index === "number" && st.lang2Index >= 0 && st.lang2Index < LANGUAGES.length) lang2Index = st.lang2Index;
    } catch { /* corrupt state -- keep defaults */ }
  }

  // The two lanes may never be on the same language; nudge lane 2 forward.
  if (lang1Index === lang2Index) lang2Index = (lang2Index + 1) % LANGUAGES.length;

  renderLanes();

  document.querySelectorAll("#lanes .lane").forEach((el) => {
    el.addEventListener("click", () => {
      const num = parseInt(el.dataset.num, 10);
      if (num === 1 || num === 2) selectLane(num);
    });
  });

  persistState();
}
initLanes();

// --- hold-to-talk capture ---

const talkState = {
  recording: false,
  uploading: false,
  stream: null,
  audioContext: null,
  scriptProcessor: null,
  source: null,
  sampleChunks: [],
};

// Dropping a silent take client-side, before it is ever POSTed. Same threshold
// and units as gemma_stt.py's SILENCE_DBFS (-50 dBFS), which the server-side
// gate in core/hearing.py also uses. A silent clip sent to the model makes it
// echo its own prompt scaffolding instead of transcribing, so never send one.
const SILENCE_DBFS = -50;

// Mean level of a Float32Array in dBFS. -Infinity for digital silence. Same
// math as gemma_stt.py's dbfs() (RMS over squared samples, 20*log10).
function dbfsOf(samples) {
  if (!samples || samples.length === 0) return -Infinity;
  let acc = 0;
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i];
    acc += s * s;
  }
  const rms = Math.sqrt(acc / samples.length);
  if (rms <= 0) return -Infinity;
  return 20 * Math.log10(rms);
}

// The one thing that actually closes the mic: stopping only the recording graph
// leaves every MediaStream track live, so the browser keeps the recording
// indicator lit. Every stream track must be stopped on every exit path (release,
// error, empty take, page hide/unload). A stream is single-use: once torn down
// it is nulled so the next take requests a fresh getUserMedia stream.
function stopStreamTracks() {
  if (talkState.stream) {
    talkState.stream.getTracks().forEach((t) => t.stop());
    talkState.stream = null;
  }
}

// Stop and disconnect the Web Audio processing chain, then close the context.
function stopAudioGraph() {
  const sp = talkState.scriptProcessor;
  talkState.scriptProcessor = null;
  if (sp) {
    try { sp.disconnect(); } catch { /* already gone */ }
    sp.onaudioprocess = null;
  }
  const src = talkState.source;
  talkState.source = null;
  if (src) {
    try { src.disconnect(); } catch { /* already gone */ }
  }
  const ctx = talkState.audioContext;
  talkState.audioContext = null;
  if (ctx && ctx.state !== "closed") {
    try { ctx.close().catch(() => {}); } catch { /* already closed */ }
  }
}

// Full teardown: stop the audio graph, every stream track, and drop any buffered
// samples. Safe to call repeatedly -- idempotent. Used on error and on page
// hide/unload, where the escape hatch is "indicator must go out, whatever is in
// flight".
function teardownCapture() {
  stopAudioGraph();
  stopStreamTracks();
  talkState.sampleChunks = [];
  talkState.recording = false;
  talk.classList.remove("recording");
  pane.classList.remove("recording");
}

async function startRecording() {
  if (talkState.uploading) return; // ignore a second press while one is uploading
  if (talkState.recording) return;

  try {
    talkState.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    showError("microphone unavailable -- " + plainError(err));
    return;
  }

  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    const ctx = new AC();
    talkState.audioContext = ctx;
    if (ctx.state === "suspended") await ctx.resume();

    const source = ctx.createMediaStreamSource(talkState.stream);
    talkState.source = source;

    const sp = ctx.createScriptProcessor(4096, 1, 1);
    talkState.scriptProcessor = sp;
    talkState.sampleChunks = [];
    sp.onaudioprocess = (e) => {
      talkState.sampleChunks.push(
        new Float32Array(e.inputBuffer.getChannelData(0)),
      );
    };

    source.connect(sp);
    sp.connect(ctx.destination);
  } catch (err) {
    // never hold a stream we cannot record with
    teardownCapture();
    showError("recording not supported on this browser -- " + plainError(err));
    return;
  }

  talkState.recording = true;
  talk.classList.add("recording");
  pane.classList.add("recording");
}

async function stopRecording() {
  if (!talkState.recording) return;
  const actualSampleRate =
    (talkState.audioContext && talkState.audioContext.sampleRate) || 16000;
  const chunks = talkState.sampleChunks;
  talkState.sampleChunks = [];
  talkState.recording = false;
  talk.classList.remove("recording");
  pane.classList.remove("recording");
  // Kill the audio graph and the stream tracks in the normal-release path so
  // the mic indicator goes out immediately, before any upload work.
  stopAudioGraph();
  stopStreamTracks();

  if (chunks.length === 0) {
    console.warn("No audio samples recorded");
    return;
  }

  const merged = getMergedSamples(chunks);
  const pcm = resample(merged, actualSampleRate, 16000);

  // Layer 1: measure the take before POSTing. Silent / near-silent audio is not
  // sent at all -- the model would only echo its own prompt back. Leave the
  // previous transcript untouched and show nothing (mic is already released).
  const level = dbfsOf(pcm);
  if (level < SILENCE_DBFS) {
    console.warn(
      "Silent take (" + level.toFixed(1) + " dBFS < " + SILENCE_DBFS + "), not sent"
    );
    return;
  }

  const blob = new Blob([pcm.buffer], { type: "application/octet-stream" });
  upload(blob);
}

// Concatenate the per-callback Float32Array chunks into one buffer.
function getMergedSamples(chunks) {
  let total = 0;
  for (const c of chunks) total += c.length;
  const merged = new Float32Array(total);
  let offset = 0;
  for (const c of chunks) {
    merged.set(c, offset);
    offset += c.length;
  }
  return merged;
}

// Naive linear-interpolation resampler -- quality is fine for speech STT.
function resample(audio, fromRate, toRate) {
  if (fromRate === toRate) return audio;
  const ratio = fromRate / toRate;
  const newLength = Math.round(audio.length / ratio);
  const result = new Float32Array(newLength);
  for (let i = 0; i < newLength; i++) {
    const pos = i * ratio;
    const index = Math.floor(pos);
    const frac = pos - index;
    const cur = audio[index];
    const next = index + 1 < audio.length ? audio[index + 1] : cur;
    result[i] = cur + frac * (next - cur);
  }
  return result;
}

function plainError(err) {
  if (typeof err === "string") return err;
  return (err && err.message) || "unknown error";
}

function fmt(ms) {
  return ms < 1000 ? (ms / 1000).toFixed(2) + "s" : (ms / 1000).toFixed(1) + "s";
}

// Render the result of a take in the orange translator panel: source card,
// translation card, timing line.
function showTake(data) {
  markUsed(); // first successful capture -- the first-use sphere is done
  const lane = LANGUAGES.find((l) => l.code === currentSource) || LANGUAGES[0];
  resting.hidden = true;
  active.hidden = false;
  srcLabel.textContent = lane.name.toUpperCase() + " (SOURCE)";
  srcText.textContent = data.source || "";
  dstLabel.textContent = "ENGLISH (TRANSLATION)";
  dstText.textContent = data.text || "";
  timing.textContent = data.ms ? "heard \u00b7 translated \u00b7 total " + fmt(data.ms) : "";
}

function showError(message) {
  resting.hidden = false;
  active.hidden = true;
  dstText.textContent = message;
  dstLabel.textContent = "ERROR";
  timing.textContent = "";
}

async function upload(blob) {
  talkState.uploading = true;
  try {
    const fd = new FormData();
    fd.append("audio", blob, "capture.pcm");
    fd.append("source", currentSource);
    const resp = await fetch("/audio", { method: "POST", body: fd });
    const data = await resp.json();
    if (data.ok) {
      showTake(data);
    } else {
      showError((data.error || "something went wrong").replace(/^.*?: /, ""));
    }
  } catch (err) {
    showError("could not reach Chotu -- " + plainError(err));
  } finally {
    talkState.uploading = false;
  }
}

// --- bindings ---

talk.addEventListener("pointerdown", (e) => {
  e.preventDefault(); // stop iOS Safari long-press selection
  talk.setPointerCapture(e.pointerId);
  startRecording();
});
talk.addEventListener("pointerup", stopRecording);
talk.addEventListener("pointercancel", stopRecording);
talk.addEventListener("pointerleave", stopRecording);

// --- whole-pane hold-to-talk (mobile only) ---
// On the phone the entire orange pane is the push-to-talk surface: press and
// hold anywhere to record, release to stop. This reuses the SAME recorder
// (startRecording / stopRecording) -- it is a second trigger, not a second
// capture path. The disc's own pointer handlers still cover desktop.
const mobileMq = window.matchMedia("(max-width: 760px)");
// Elements that are tap targets for something else must never start a hold.
function isHoldExcluded(target) {
  return !!target.closest("#gear, #lanes, #gear-panel");
}
pane.addEventListener("pointerdown", (e) => {
  if (!mobileMq.matches) return; // desktop keeps the disc + Z/Space
  if (isHoldExcluded(e.target)) return; // gear / lanes / settings are taps
  e.preventDefault(); // stop iOS long-press selection / text drag
  pane.setPointerCapture(e.pointerId);
  startRecording();
});
pane.addEventListener("pointerup", stopRecording);
pane.addEventListener("pointercancel", stopRecording);
pane.addEventListener("pointerleave", stopRecording);

// Hold-to-talk on the desktop: Space and Z (like the Gemma Translator), plus the
// numbered lanes. Auto-repeat is ignored so holding a key only starts a capture
// once, and the talk keys never fire while the user is typing.
let spaceDown = false;
let zDown = false;

// Never hijack a keypress aimed at a control that consumes it: a text input or
// a textarea (the numbered lanes 1/2 and Z/Space must not fire while typing).
function isTypingTarget(e) {
  const tag = e.target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

function talkKeyDown(e, key) {
  const already = key === "z" ? zDown : spaceDown;
  if (e.repeat || already || isTypingTarget(e)) return;
  if (key === "z") zDown = true;
  else spaceDown = true;
  e.preventDefault();
  startRecording();
}

function talkKeyUp(key) {
  // Release the latch and stop on key-up, even if focus moved between the
  // down and up events. stopRecording() is a no-op when nothing is held.
  if (key === "z") zDown = false;
  else spaceDown = false;
  stopRecording();
}

document.addEventListener("keydown", (e) => {
  if (e.key === "1") { if (!isTypingTarget(e)) selectLane(1); }
  else if (e.key === "2") { if (!isTypingTarget(e)) selectLane(2); }
  else if (e.key === "ArrowLeft") { if (!isTypingTarget(e)) { e.preventDefault(); rotateActiveLane(-1); } }
  else if (e.key === "ArrowRight") { if (!isTypingTarget(e)) { e.preventDefault(); rotateActiveLane(1); } }
  else if (e.key === "z" || e.key === "Z") talkKeyDown(e, "z");
  else if (e.code === "Space") talkKeyDown(e, "space");
});
document.addEventListener("keyup", (e) => {
  if (e.key === "z" || e.key === "Z") talkKeyUp("z");
  else if (e.code === "Space") talkKeyUp("space");
});

// Page-hidden/unload teardown: if the user switches away or closes the tab while
// holding Z/Space, the browser would otherwise keep the mic on and the recording
// indicator lit. Tearing down on pagehide and on becoming hidden guarantees the
// indicator goes out on these exit paths too.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) teardownCapture();
});
window.addEventListener("pagehide", teardownCapture);

// --- telemetry + camera (right pane) ---

const camPane = $("cam-pane");
const cam = $("cam");
const batteryPct = $("battery-pct");
const batteryVolt = $("battery-volt");
const SAG_DELTA = 0.4; // volts
const SAG_WINDOW_MS = 30000;

// 30 s ring buffer of voltages so a brown-out (which has already taken the Pi
// down mid-move) is caught early and flagged on the BATTERY row.
const sagRing = [];
function setBattery(percent, voltage) {
  batteryPct.textContent = Math.round(percent) + "%";
  if (typeof voltage === "number") {
    batteryVolt.textContent = voltage.toFixed(2) + "V";
    const now = Date.now();
    sagRing.push({ t: now, v: voltage });
    while (sagRing.length && sagRing[0].t < now - SAG_WINDOW_MS) sagRing.shift();
    const maxV = Math.max(...sagRing.map((r) => r.v));
    batteryPct.classList.toggle("sag", maxV - voltage >= SAG_DELTA);
  }
}

// Bootstrap the meter once, before the first pushed battery event.
async function bootstrapBattery() {
  try {
    const resp = await fetch("/api/battery");
    const data = await resp.json();
    let pct, volt;
    if (data && data.ok && data.result) {
      pct = data.result.percent;
      volt = data.result.voltage;
    } else if (data && typeof data.percent === "number") {
      pct = data.percent;
      volt = data.voltage;
    }
    if (typeof pct === "number") setBattery(pct, volt);
  } catch {
    /* leave the placeholder -- the first SSE event fills it */
  }
}

// Battery lives on the SSE stream, which also carries the live transcript the
// console no longer renders; only battery is consumed here.
const es = new EventSource("/events");
es.onmessage = (msg) => {
  try {
    const e = JSON.parse(msg.data);
    if (e.type === "battery" && typeof e.percent === "number") setBattery(e.percent, e.voltage);
  } catch {
    /* bad frame -- drop it */
  }
};

// Camera: when the Pi's camera is off or the robot is down the stream fails
// and the image goes blank. Show a placeholder and retry every 5 s so the
// console never looks broken because Chotu is charging.
let camRetry = null;
cam.addEventListener("error", () => {
  camPane.classList.add("offline");
  if (!camRetry) {
    camRetry = setInterval(() => {
      cam.src = "/stream?" + Date.now();
    }, 5000);
  }
});
cam.addEventListener("load", () => {
  camPane.classList.remove("offline");
  if (camRetry) {
    clearInterval(camRetry);
    camRetry = null;
  }
});

bootstrapBattery();

// --- settings gear + stop ---

const gear = $("gear");
const panel = $("gear-panel");
const gearClose = $("gear-close");
const gearModel = $("gear-model");
const gearBridge = $("gear-bridge");
const stopBtn = $("stop");
let panelOpen = false;
let settingsFilled = false;

function openPanel() {
  panelOpen = true;
  panel.hidden = false;
  requestAnimationFrame(() => panel.classList.add("open"));
  fillSettings();
  gearClose.focus();
}

function closePanel() {
  panelOpen = false;
  panel.classList.remove("open");
  gear.focus();
  setTimeout(() => {
    if (!panelOpen) panel.hidden = true;
  }, 200);
}

async function fillSettings() {
  if (settingsFilled) return;
  try {
    const resp = await fetch("/api/config");
    const data = await resp.json();
    if (data.model) gearModel.textContent = data.model;
    if (data.bridge) gearBridge.textContent = data.bridge;
    settingsFilled = true;
  } catch {
    /* leave the lines empty -- not worth an error entry */
  }
}

gear.addEventListener("click", () => (panelOpen ? closePanel() : openPanel()));
gearClose.addEventListener("click", closePanel);
stopBtn.addEventListener("click", async () => {
  try {
    await fetch("/stop", { method: "POST" });
  } catch {
    showError("could not reach Chotu to stop");
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && panelOpen) closePanel();
});
