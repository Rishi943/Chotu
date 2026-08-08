"""Resident Piper TTS service for Chotu.

Loads the en_US-libritts_r-medium multi-speaker model ONCE at startup with
speaker 668 and holds it in memory. One endpoint: text in, WAV bytes out.

The point is latency. Spawning a fresh ``piper.exe`` per line costs ~2445 ms
(first-audio) because of process spawn plus ONNX model load; holding the model
in a resident process drops that to the ~200 ms synthesis time alone.

Port 8101 (8099 is llama-server, 8888 the console, 7000 the Pi bridge, 3000 the
translator, 8890 a test server). Binds 127.0.0.1 -- local only, never exposed.

Usage:
    python -m core.piper_server
    # or
    python core/piper_server.py
"""

import json
import os
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The reels' shared paths module lives in the picrawler-vfx repo. Nudge sys.path
# so `import paths as P` resolves and the default model path comes from P (no
# voice/model path is hard-coded here). Same bootstrap the bench scripts use.
sys.path.insert(0, r"E:\AI\picrawler-vfx\scripts")

try:
    import paths as P
except ImportError:  # pragma: no cover - only when launched without the harness path
    P = None

from piper import PiperVoice
from piper.config import SynthesisConfig

# --------------------------------------------------------------------------- #
# Configuration (read at import time, so overridable from the environment)
# --------------------------------------------------------------------------- #

HOST = "127.0.0.1"
PORT = 8101

_REEL_MODEL = "en_US-libritts_r-medium.onnx"


def _default_model() -> str:
    """The reel voice model. Prefer the shared paths module; else the env."""
    if P is not None:
        return os.path.join(P.PALIV_VOICES, _REEL_MODEL)
    # Fallback when launched without the shared paths module on sys.path.
    return os.environ.get("PIPER_MODEL", "")


MODEL = os.environ.get("PIPER_MODEL", "") or _default_model()
# Reel voice is LibriTTS speaker 668. The voice lives in ONE place -- here -- so
# the robot cannot drift out of sync with what the reels say.
SPEAKER = int(os.environ.get("PIPER_SPEAKER", "668"))

# Map the CLI-style pacing flags that local_speak forwards via PALIV_PIPER_ARGS
# onto the streaming synthesizer's SynthesisConfig fields.
_FLAG_MAP = {
    "--length-scale": "length_scale",
    "--volume": "volume",
    "--noise-scale": "noise_scale",
    "--noise-w-scale": "noise_w_scale",
    "--sentence-silence": "sentence_silence",
}


def _parse_piper_args(args, speaker: int) -> SynthesisConfig:
    """Turn a PALIV_PIPER_ARGS-style flag list into a SynthesisConfig.

    The speaker is owned by the service (this module), so any ``-s``/``--speaker``
    the client forwards is ignored -- the reel voice cannot be overridden by the
    caller. Only the pacing flags are honoured.
    """
    cfg = SynthesisConfig(speaker_id=speaker)
    i = 0
    while i < len(args):
        flag = args[i]
        if flag in _FLAG_MAP and i + 1 < len(args):
            value = float(args[i + 1])
            setattr(cfg, _FLAG_MAP[flag], value)
            i += 2
            continue
        # -s / --speaker and anything unknown are skipped.
        i += 1
    return cfg


def _wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap 16-bit mono PCM in a standard 44-byte RIFF/WAVE header."""
    data_size = len(pcm)
    byte_rate = sample_rate * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, 1,
        sample_rate, byte_rate, 2, 16,
        b"data", data_size,
    )
    return header + pcm
class PiperServer:
    """Holds one loaded PiperVoice and synthesizes on demand."""

    def __init__(self, model: str = MODEL, speaker: int = SPEAKER):
        self.model = model
        self.speaker = speaker
        self._voice = PiperVoice.load(model)
        # ONNX Runtime sessions are not guaranteed thread-safe; serialize synthesis.
        self._synth_lock = threading.Lock()

    # -- synthesis ---------------------------------------------------------- #

    def synthesize_wav(self, text: str, args) -> tuple[bytes, int]:
        """Synthesize text to WAV bytes. Returns (wav_bytes, elapsed_ms)."""
        start = time.time()
        cfg = _parse_piper_args(args, self.speaker)
        with self._synth_lock:
            chunks = list(self._voice.synthesize(text, cfg))
        if not chunks:
            return b"", int((time.time() - start) * 1000)
        sample_rate = chunks[0].sample_rate
        silence = getattr(cfg, "sentence_silence", None) or 0.0
        parts = []
        for i, chunk in enumerate(chunks):
            if i > 0 and silence > 0:
                n = int(sample_rate * silence)
                parts.append(b"\x00\x00" * n)
            parts.append(chunk.audio_int16_bytes)
        pcm = b"".join(parts)
        wav = _wav_bytes(pcm, sample_rate)
        return wav, int((time.time() - start) * 1000)

    def health(self) -> dict:
        return {
            "ok": True,
            "service": "piper_server",
            "model": self.model,
            "speaker": self.speaker,
            "port": PORT,
        }


_MANAGER = {"server": None}


def _get_server() -> PiperServer:
    if _MANAGER["server"] is None:
        _MANAGER["server"] = PiperServer()
    return _MANAGER["server"]


class _Handler(BaseHTTPRequestHandler):
    server_version = "PiperServer/1.0"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _send_wav(self, wav: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav)))
        self.end_headers()
        self.wfile.write(wav)

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, _get_server().health())
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path not in ("/", "/synthesize"):
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            text = payload.get("text", "")
            args = payload.get("args") or []
            if not text:
                self._send_json(400, {"ok": False, "error": "text is required"})
                return
            wav, elapsed_ms = _get_server().synthesize_wav(text, args)
            self._send_wav(wav)
        except Exception as e:  # never let a synthesis error crash the worker
            self._send_json(500, {"ok": False, "error": str(e)})


def main():
    if not MODEL:
        raise RuntimeError(
            "no model path: set PIPER_MODEL or run with the shared paths module "
            "on sys.path (which supplies core/voices)"
        )
    server = _get_server()
    httpd = ThreadingHTTPServer((HOST, PORT), _Handler)
    print(f"piper_server up on {HOST}:{PORT} model={server.model} speaker={server.speaker}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()