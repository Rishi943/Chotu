"""Measure Piper's time-to-first-audio on this machine. Decides Task 8's
streaming question: median first-audio under 1.0 s means whole-utterance
synthesis ships as-is; over 1.0 s means sentence streaming gets built.

Usage:
    python scripts/bench/piper_latency.py            # also times POST /play_wav
    python scripts/bench/piper_latency.py --no-pi     # Piper only, robot unreachable
"""
import argparse
import os
import statistics
import struct
import subprocess
import sys
import time

MODEL = os.environ.get(
    "LOCALIS_PIPER_MODEL",
    "E:/AI/paliv/core/voices/en_GB-northern_english_male-medium.onnx",
)
PI_HOST = os.environ.get("PI_HOST", "http://192.168.0.190:7000")
PIPER_EXE = os.environ.get(
    "PIPER_EXE", "C:/Users/rushi/paliv-win-venv/Scripts/piper.exe"
)

SENTENCES = [
    "Battery's at sixty two percent.",
    "I heard you say walk forward two steps, so that's what I'm doing now.",
    "Cho two here. My four legs and twelve servos are all working, camera's "
    "on, and I'm ready for whatever Roo-shi wants me to try next.",
]


def first_audio_ms(text: str) -> tuple[float, bytes]:
    """Time from process start to Piper's first stdout bytes. Returns (ms, full_pcm)."""
    start = time.time()
    proc = subprocess.Popen(
        [PIPER_EXE, "--model", MODEL, "--output-raw"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    proc.stdin.write(text.encode())
    proc.stdin.close()
    first_chunk = proc.stdout.read(4096)  # blocks until Piper writes something
    t_first = (time.time() - start) * 1000
    rest = proc.stdout.read()
    proc.wait()
    return t_first, first_chunk + rest


def post_to_pi(pcm: bytes) -> float:
    import httpx
    sr = 22050
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE",
                          b"fmt ", 16, 1, 1, sr, sr * 2, 2, 16, b"data", len(pcm))
    start = time.time()
    httpx.post(f"{PI_HOST}/play_wav", content=header + pcm,
               headers={"Content-Type": "audio/wav"}, timeout=10.0)
    return (time.time() - start) * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pi", action="store_true", help="skip the /play_wav POST")
    args = ap.parse_args()

    all_medians = []
    for i, text in enumerate(SENTENCES, 1):
        print(f"[sentence {i}/3, {len(text.split())} words] {text[:50]}...")
        runs = []
        for r in range(3):
            ms, pcm = first_audio_ms(text)
            runs.append(ms)
            print(f"  run {r + 1}/3: first-audio {ms:.0f} ms")
            if not args.no_pi:
                try:
                    post_ms = post_to_pi(pcm)
                    print(f"    POST /play_wav: {post_ms:.0f} ms")
                except Exception as e:
                    print(f"    POST /play_wav FAILED (expected if robot is down): {e}")
        med = statistics.median(runs)
        all_medians.append(med)
        print(f"  median: {med:.0f} ms\n")

    overall = statistics.median(all_medians)
    print(f"OVERALL MEDIAN first-audio: {overall:.0f} ms")
    sys.exit(0)


if __name__ == "__main__":
    main()
