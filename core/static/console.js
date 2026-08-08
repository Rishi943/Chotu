// Chotu console — page entry module. Holds the talk capture; later tasks add
// the SSE transcript, camera/battery/stop, and the settings panel.

const $ = (id) => document.getElementById(id);

const talk = $("talk");
const hint = $("hint");
const transcript = $("transcript");
const langEl = $("lang");

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
  recorder: null,
  chunks: [],
};

async function startRecording() {
  if (talkState.uploading) return; // ignore a second press while one is uploading
  if (talkState.recording) return;

  if (!talkState.stream) {
    try {
      talkState.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      appendEntry("err", "microphone unavailable — " + plainError(err));
      return;
    }
  }

  const mime = pickMime();
  let recorder;
  try {
    recorder = mime
      ? new MediaRecorder(talkState.stream, { mimeType: mime })
      : new MediaRecorder(talkState.stream);
  } catch (err) {
    // mimeType rejected even though isTypeSupported said yes — retry without it
    try {
      recorder = new MediaRecorder(talkState.stream);
    } catch (err2) {
      appendEntry("err", "recording not supported on this browser");
      return;
    }
  }

  talkState.chunks = [];
  recorder.ondataavailable = (e) => {
    if (e.data && e.data.size) talkState.chunks.push(e.data);
  };
  recorder.onerror = () => {
    talkState.recording = false;
    talk.classList.remove("recording");
  };
  recorder.onstop = () => {
    const blob = new Blob(talkState.chunks, { type: recorder.mimeType || "audio/webm" });
    upload(blob);
  };
  recorder.start();
  talkState.recorder = recorder;
  talkState.recording = true;
  talk.classList.add("recording");
}

function stopRecording() {
  if (!talkState.recording) return;
  talkState.recording = false;
  talk.classList.remove("recording");
  try {
    talkState.recorder.stop();
    talkState.recorder = null;
  } catch {
    talkState.recorder = null;
  }
}

function pickMime() {
  if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) return "audio/webm;codecs=opus";
  if (MediaRecorder.isTypeSupported("audio/mp4")) return "audio/mp4"; // Safari
  return "";
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
    fd.append("audio", blob, "capture.webm");
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

// space bar on the desktop, ignoring auto-repeat
let spaceDown = false;
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || e.repeat || spaceDown) return;
  spaceDown = true;
  e.preventDefault();
  startRecording();
});
document.addEventListener("keyup", (e) => {
  if (e.code !== "Space") return;
  spaceDown = false;
  stopRecording();
});

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