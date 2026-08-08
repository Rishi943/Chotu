// Chotu console — page entry module. Holds the talk capture; later tasks add
// the SSE transcript, camera/battery/stop, and the settings panel.

const $ = (id) => document.getElementById(id);

const talk = $("talk");
const hint = $("hint");
const transcript = $("transcript");
const langEl = $("lang");
const sourceLangEl = $("source-lang");

// The source language is an explicit user choice -- the backend never asks the
// model to detect it. Adding a language is a one-line addition here.
const SOURCE_LANGS = [
  { code: "mr", name: "Marathi" },
  { code: "hi", name: "Hindi" },
  { code: "ja", name: "Japanese" },
  { code: "en", name: "English" },
];
const SOURCE_KEY = "chotu.sourceLang";

function initSourceLang() {
  for (const lang of SOURCE_LANGS) {
    const opt = document.createElement("option");
    opt.value = lang.code;
    opt.textContent = lang.name;
    sourceLangEl.appendChild(opt);
  }
  let saved = null;
  try {
    saved = localStorage.getItem(SOURCE_KEY);
  } catch { /* localStorage unavailable -- use the default */ }
  const chosen =
    SOURCE_LANGS.find((l) => l.code === saved) || SOURCE_LANGS[0];
  sourceLangEl.value = chosen.code;
  sourceLangEl.addEventListener("change", () => {
    try {
      localStorage.setItem(SOURCE_KEY, sourceLangEl.value);
    } catch { /* non-fatal */ }
  });
}
initSourceLang();

// --- transcript helpers (shared by the capture and the SSE stream) ---

function appendEntry(className, inner) {
  const el = document.createElement("div");
  el.className = className;
  el.innerHTML = inner;
  transcript.appendChild(el);
  transcript.scrollTop = transcript.scrollHeight;
  return el;
}

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

// Mic-capture wiring ported from the Gemma Translator's useAudioRecorder: a
// ScriptProcessorNode grabs raw Float32 PCM and we resample it to 16 kHz mono
// on release — no MediaRecorder/webm/opus, so the backend always receives bare
// PCM it can wrap in a real WAV. ScriptProcessorNode is deprecated but needs no
// separately-served AudioWorklet module, so it is the right call for short
// push-to-talk clips on the console's Chromium.

// The one thing that actually closes the mic: stopping only the recording graph
// leaves every MediaStream track live, so the browser keeps the recording
// indicator lit. Ported from the Gemma Translator's capture wiring — every
// stream track must be stopped on every exit path (release, error, empty take,
// page hide/unload). A stream is single-use: once torn down it is nulled so the
// next take requests a fresh getUserMedia stream.
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
// samples. Safe to call repeatedly — idempotent. Used on error and on page
// hide/unload, where the escape hatch is "indicator must go out, whatever is in
// flight".
function teardownCapture() {
  stopAudioGraph();
  stopStreamTracks();
  talkState.sampleChunks = [];
  talkState.recording = false;
  talk.classList.remove("recording");
}

async function startRecording() {
  if (talkState.uploading) return; // ignore a second press while one is uploading
  if (talkState.recording) return;

  try {
    talkState.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    appendEntry("err", "microphone unavailable — " + plainError(err));
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
    appendEntry("err", "recording not supported on this browser — " + plainError(err));
    return;
  }

  talkState.recording = true;
  talk.classList.add("recording");
}

async function stopRecording() {
  if (!talkState.recording) return;
  const actualSampleRate =
    (talkState.audioContext && talkState.audioContext.sampleRate) || 16000;
  const chunks = talkState.sampleChunks;
  talkState.sampleChunks = [];
  talkState.recording = false;
  talk.classList.remove("recording");
  // Kill the audio graph and the stream tracks in the normal-release path so
  // the mic indicator goes out immediately, before any upload work.
  stopAudioGraph();
  stopStreamTracks();

  if (chunks.length === 0) {
    console.warn("No audio samples recorded");
    return;
  }

  // Ported from the Gemma Translator: merge the per-callback chunks and
  // resample to 16 kHz mono, the shape the backend expects for a WAV.
  const merged = getMergedSamples(chunks);
  const pcm = resample(merged, actualSampleRate, 16000);
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

// Naive linear-interpolation resampler — quality is fine for speech STT.
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

async function upload(blob) {
  talkState.uploading = true;
  const pending = appendEntry("you pending", '<span class="mono dim">…</span>');

  try {
    const fd = new FormData();
    fd.append("audio", blob, "capture.pcm");
    fd.append("source", sourceLangEl.value);
    const resp = await fetch("/audio", { method: "POST", body: fd });
    const data = await resp.json();
    if (data.ok) {
      pending.className = "you";
      pending.innerHTML =
        "<span class='txt'></span><span class='mono meta'></span>";
      pending.querySelector(".txt").textContent = data.text || "";
      const meta = pending.querySelector(".meta");
      meta.textContent = data.language ? data.language + (data.ms ? " · " + data.ms + "ms" : "") : "";
      if (data.language) langEl.textContent = data.language;
    } else {
      pending.remove();
      appendEntry("err", (data.error || "something went wrong").replace(/^.*?: /, ""));
    }
  } catch (err) {
    pending.remove();
    appendEntry("err", "could not reach Chotu — " + plainError(err));
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

// Hold-to-talk on the desktop: Space (existing) and Z (like the Gemma
// Translator). Auto-repeat is ignored so holding a key only starts a capture
// once, and neither key fires while the user is typing or inside the new
// source-language select.
let spaceDown = false;
let zDown = false;

// Never hijack a keypress aimed at a control that consumes it: a text input,
// a textarea, or the native language <select> (Space opens that dropdown).
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
  if (e.key === "z" || e.key === "Z") talkKeyDown(e, "z");
  else if (e.code === "Space") talkKeyDown(e, "space");
});
document.addEventListener("keyup", (e) => {
  if (e.key === "z" || e.key === "Z") talkKeyUp("z");
  else if (e.code === "Space") talkKeyUp("space");
});

// Page-hidden/unload teardown: if the user switches away or closes the tab while
// holding Z, the browser would otherwise keep the mic on and the recording
// indicator lit. Tearing down on pagehide and on becoming hidden guarantees the
// indicator goes out on these exit paths too.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) teardownCapture();
});
window.addEventListener("pagehide", teardownCapture);

// --- live transcript from the SSE stream ---

const batteryFill = $("battery-fill");
const batteryPct = $("battery-pct");
const MAX_ENTRIES = 200;

function trimTranscript() {
  while (transcript.children.length > MAX_ENTRIES) {
    transcript.removeChild(transcript.firstElementChild);
  }
}

function scrollToNewestIfAtBottom() {
  const nearBottom =
    transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 40;
  if (nearBottom) transcript.scrollTop = transcript.scrollHeight;
}

// The brain's GUI stream emits these types. Everything else is ignored so an
// event from elsewhere in the brain never renders as garbage.
function handleEvent(e) {
  switch (e.type) {
    case "user": // a live human utterance
      if (!e.text) return;
      appendEntry("you", '<span class="txt"></span>');
      transcript.lastElementChild.querySelector(".txt").textContent = e.text;
      break;

    case "speak": // Chotu's reply
      if (!e.text) return;
      appendEntry("bot", "");
      transcript.lastElementChild.textContent = e.text;
      break;

    case "tool_call": {
      const arg = toolArg(e.tool, e.args);
      appendEntry("act", "▸ " + e.tool + (arg ? " " + arg : ""));
      break;
    }

    case "battery":
      if (typeof e.percent === "number") setBattery(e.percent, e.voltage);
      break;

    case "ping":
      break;

    default: // monologue, think, image, face, ptt, event, ... — ignore
      break;
  }
  trimTranscript();
  scrollToNewestIfAtBottom();
}

function toolArg(tool, args) {
  args = args || {};
  if (tool === "move") return args.direction;
  if (tool === "act") return args.name;
  if (tool === "sense") return args.what;
  return ""; // say, read, and anything else
}

// EventSource reconnects automatically (default ~3 s), matching the old page.
const es = new EventSource("/events");
es.onmessage = (msg) => {
  try {
    handleEvent(JSON.parse(msg.data));
  } catch {
    /* bad frame — drop it */
  }
};

// --- camera, battery, and the stop control ---

const stage = $("stage");
const cam = $("cam");
const stopBtn = $("stop");
const SAG_DELTA = 0.4; // volts
const SAG_WINDOW_MS = 30000;

// 30 s ring buffer of voltages so a brown-out (which has already taken the Pi
// down mid-move) is caught early.
const sagRing = [];
function setBattery(percent, voltage) {
  batteryFill.style.width = Math.max(0, Math.min(100, percent)) + "%";
  batteryPct.textContent = Math.round(percent) + "%";

  if (typeof voltage === "number") {
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

// Camera: when the Pi's camera is off or the robot is down the stream fails
// and the image goes blank. Show a placeholder and retry every 5 s so the
// console never looks broken because Chotu is charging.
let camRetry = null;
cam.addEventListener("error", () => {
  stage.classList.add("offline");
  if (!camRetry) {
    camRetry = setInterval(() => {
      cam.src = "/stream?" + Date.now();
    }, 5000);
  }
});
cam.addEventListener("load", () => {
  stage.classList.remove("offline");
  if (camRetry) {
    clearInterval(camRetry);
    camRetry = null;
  }
});

// E-stop: freeze the robot and clear any staged follow-up move.
stopBtn.addEventListener("click", async () => {
  try {
    await fetch("/stop", { method: "POST" });
  } catch {
    appendEntry("err", "could not reach Chotu to stop");
  }
});

bootstrapBattery();

// --- settings gear -- near-empty, phase 2's tick rate lives here later ---

const gear = $("gear");

const panel = document.createElement("div");
panel.id = "gear-panel";
panel.hidden = true;
panel.innerHTML =
  '<div class="gear-head"><span class="mono">SETTINGS</span>' +
  '<button id="gear-close" aria-label="Close settings">&#10005;</button></div>' +
  '<div class="gear-row"><span class="gear-label">model</span>' +
  '<span id="gear-model" class="mono"></span></div>' +
  '<div class="gear-row"><span class="gear-label">bridge</span>' +
  '<span id="gear-bridge" class="mono"></span></div>';
transcript.prepend(panel);

const gearClose = panel.querySelector("#gear-close");
const gearModel = panel.querySelector("#gear-model");
const gearBridge = panel.querySelector("#gear-bridge");
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
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && panelOpen) closePanel();
});