"""Voice input: wake word detection + Whisper STT."""

import asyncio
import os
import queue
import threading
import numpy as np

# --- Config ---

WAKE_WORD_MODEL_PATH = os.getenv(
    "CHOTU_WAKE_WORD_MODEL",
    os.path.expanduser("~/Rishi/AI/Chotu/models/hey_jarvis_v0.1.onnx"),
)
WHISPER_MODEL_SIZE = os.getenv("CHOTU_WHISPER_MODEL", "small")
WAKE_THRESHOLD = float(os.getenv("CHOTU_WAKE_THRESHOLD", "0.5"))
MIC_DEVICE = os.getenv("CHOTU_MIC_DEVICE")  # None = sounddevice default; set to device index/name for ReSpeaker
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280       # 80ms at 16kHz — openWakeWord's expected chunk size
SILENCE_TIMEOUT_S = 1.5    # seconds of silence after speech ends recording
MAX_RECORD_S = 10
ENERGY_SILENCE = 0.01      # RMS below this = silence


# --- Pure audio utilities ---

def _audio_to_int16(chunk: np.ndarray) -> np.ndarray:
    """Convert float32 audio [-1, 1] to int16 for openWakeWord."""
    scaled = chunk * 32768.0
    return np.clip(scaled, -32768, 32767).astype(np.int16)


def _is_speech(chunk: np.ndarray, threshold: float = ENERGY_SILENCE) -> bool:
    """Return True if RMS energy of chunk exceeds threshold."""
    if chunk.size == 0:
        return False
    return float(np.sqrt(np.mean(chunk ** 2))) > threshold


# --- Lazy model singletons ---

WhisperModel = None  # set on first use by _get_whisper
OWWModel = None      # set on first use by _get_oww

_whisper_lock = threading.Lock()
_oww_lock = threading.Lock()

_whisper_model = None
_oww_model = None


def _get_whisper():
    global _whisper_model, WhisperModel
    with _whisper_lock:
        if WhisperModel is None:
            from faster_whisper import WhisperModel as _WM
            WhisperModel = _WM
        if _whisper_model is None:
            print("  [voice] Loading Whisper (first call, may take a moment)...")
            _whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _whisper_model


def _get_oww():
    global _oww_model, OWWModel
    with _oww_lock:
        if OWWModel is None:
            from openwakeword.model import Model as _OWW
            OWWModel = _OWW
        if _oww_model is None:
            _oww_model = OWWModel(wakeword_models=[WAKE_WORD_MODEL_PATH], inference_framework="onnx")
    return _oww_model


# --- Blocking listener ---

def _blocking_listen_and_transcribe() -> str:
    """Block until wake word heard, record utterance, return transcribed text."""
    import sounddevice
    audio_q: queue.Queue = queue.Queue()

    def _cb(indata, frames, time, status):
        audio_q.put(indata[:, 0].copy())

    oww = _get_oww()
    oww.reset()

    stream_kwargs = dict(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32",
        blocksize=CHUNK_SAMPLES, callback=_cb,
    )
    if MIC_DEVICE is not None:
        stream_kwargs["device"] = int(MIC_DEVICE) if MIC_DEVICE.isdigit() else MIC_DEVICE

    with sounddevice.InputStream(**stream_kwargs):
        # Phase 1: wait for wake word
        print("  [voice] Waiting for 'Hey Jarvis'...")
        while True:
            try:
                chunk = audio_q.get(timeout=5.0)
            except queue.Empty:
                continue
            scores = oww.predict(_audio_to_int16(chunk))
            if max(scores.values()) >= WAKE_THRESHOLD:
                print("  [voice] Wake word! Speak now...")
                break

        # Phase 2: record until silence
        recorded: list[np.ndarray] = []
        silence_chunks = 0
        silence_limit = int(SILENCE_TIMEOUT_S * SAMPLE_RATE / CHUNK_SAMPLES)
        max_chunks = int(MAX_RECORD_S * SAMPLE_RATE / CHUNK_SAMPLES)
        has_speech = False

        for _ in range(max_chunks):
            try:
                chunk = audio_q.get(timeout=5.0)
            except queue.Empty:
                break
            recorded.append(chunk)
            if _is_speech(chunk):
                has_speech = True
                silence_chunks = 0
            elif has_speech:
                silence_chunks += 1
                if silence_chunks >= silence_limit:
                    break

    if not recorded or not has_speech:
        return ""

    audio = np.concatenate(recorded)
    segments, _ = _get_whisper().transcribe(audio, language="en", beam_size=5)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    print(f"  [voice] Heard: {text!r}")
    return text


# --- Public async API ---

async def listen_and_transcribe() -> str:
    """Async wrapper: runs blocking listener in thread pool."""
    return await asyncio.to_thread(_blocking_listen_and_transcribe)
