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