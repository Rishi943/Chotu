"""Voice input: wake word detection + Whisper STT."""

import asyncio
import os
import queue
import numpy as np

# --- Config ---

WAKE_WORD_MODEL_PATH = os.getenv(
    "CHOTU_WAKE_WORD_MODEL",
    os.path.expanduser("~/.local/share/localis/wakeword_models/hey_jarvis_v0.1.onnx"),
)
WHISPER_MODEL_SIZE = os.getenv("CHOTU_WHISPER_MODEL", "small")
WAKE_THRESHOLD = float(os.getenv("CHOTU_WAKE_THRESHOLD", "0.5"))
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
