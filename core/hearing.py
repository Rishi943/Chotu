"""One-call hearing: audio in, English text + source-language name out.

The source language is an explicit, user-chosen setting -- never something the
model detects. The transcribe-then-translate prompt is copied VERBATIM from the
model card via `E:/AI/gemma-translator/backend/gemma_stt.py` (`_chat` and
`_BOTH`). That module always knows the source language ahead of time
(`src="mr"` etc); this one now does too, so the model is TOLD the language
instead of asked to name it.

Measured 2026-08-08 on Gemma 4 E2B QAT: asked to identify the language from
the audio, the model calls Marathi "Hindi" 3 runs out of 3, so two-stage
detection is dead. Naming the source explicitly is the only approach that has
ever worked on Marathi. Do not touch the transcribe wording style -- every
invented rephrasing so far has made the model transliterate instead of
translate.
"""

from __future__ import annotations

import array as _array
import base64
import io
import os
import struct
import time
import wave

import httpx

BASE_URL = os.getenv("PALIV_BRAIN_URL", "http://127.0.0.1:8099/v1")
MODEL = os.getenv("PALIV_BRAIN_MODEL", "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf")

# Google's model card wording, word-for-word from gemma_stt.py's _BOTH. The
# source language is filled in from the user's explicit choice; the destination
# is always English.
_PROMPT = (
    "Transcribe the following speech segment in {src}, then translate it into "
    "{dst}.\nWhen formatting the answer, first output the transcription in "
    "{src}, then one newline, then output the string '{dst}: ', then the "
    "translation in {dst}."
)

# Fallback / default when the client sends nothing, so an old client keeps
# working. Marathi is the one language this was measured to succeed on.
DEFAULT_SOURCE = "mr"

LANG_NAME = {
    "mr": "Marathi", "hi": "Hindi", "en": "English", "es": "Spanish",
    "ar": "Arabic", "ja": "Japanese", "zh": "Chinese", "ko": "Korean",
}


def build_prompt(source, dst="en"):
    """The known-source prompt for a language code.

    Unknown codes fall back to Marathi (DEFAULT_SOURCE), matching what the
    endpoint does when the client sends nothing.
    """
    src_name = LANG_NAME.get(source, LANG_NAME[DEFAULT_SOURCE])
    dst_name = LANG_NAME.get(dst, "English")
    return _PROMPT.format(src=src_name, dst=dst_name)


def parse_hearing(reply, src_name="Marathi", dst_name="English"):
    """Split the model's reply into {"text", "source", "language"}. Never raises.

    text is the English translation (what the brain consumes). source is the
    verbatim transcription in the source language (the Devanagari, for
    Marathi/Hindi) -- the model emits it on the same line as the `English:`
    marker, and the Gemma Translator renders it, so it must not be thrown away.
    language is the source-language name we already knew going in -- the model
    no longer names it, so we label the reply with the user's explicit choice.
    """
    reply = (reply or "").strip()
    if not reply:
        return {"text": "", "source": "", "language": src_name}

    marker = dst_name + ":"
    if marker in reply:
        source, _, translation = reply.partition(marker)
        return {
            "text": translation.strip(),
            "source": source.strip(),
            "language": src_name,
        }

    lines = [l.strip() for l in reply.splitlines() if l.strip()]
    if len(lines) >= 2:
        # No `English:` marker but a multi-line reply: the first line is almost
        # always the source transcription, the rest the translation.
        return {
            "text": " ".join(lines[1:]),
            "source": lines[0],
            "language": src_name,
        }
    return {"text": reply, "source": reply, "language": src_name}


def pcm_to_wav(pcm_bytes, rate=16000):
    """Wrap raw little-endian float32 PCM from the browser in a 16-bit WAV.

    The frontend posts a bare Float32Array buffer (already resampled to `rate`
    Hz mono); the model wants a real file. Converted to 16-bit signed PCM (1
    channel, 2 bytes wide, `rate` Hz) -- what every speech model expects and
    what halves the payload. Stdlib `wave` only: no numpy, no ffmpeg.
    """
    n = len(pcm_bytes) // 4
    samples = struct.unpack("<%df" % n, pcm_bytes[: n * 4])
    pcm16 = _array.array("h")
    for s in samples:
        # Clip before scaling: a float32 buffer can exceed +-1.0 and would wrap
        # around into loud noise when cast to int16.
        if s > 1.0:
            s = 1.0
        elif s < -1.0:
            s = -1.0
        pcm16.append(int(s * 32767.0))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


async def hear(audio_bytes, mime, source=DEFAULT_SOURCE):
    """POST audio to llama-server (E2B, :8099) and return {"text","language","ms"}.

    `audio_bytes` is the browser's raw little-endian float32 PCM (16 kHz mono).
    It is wrapped into a *real* WAV here via pcm_to_wav, so the model always
    gets a verified `wav` blob -- never a guessed format. `mime` is kept for
    the route's bookkeeping but is no longer trusted to label the payload.

    The source language is the user's explicit choice (default Marathi); the
    prompt names it, so the model never has to guess.
    """
    wav_bytes = pcm_to_wav(audio_bytes, 16000)
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": build_prompt(source)},
            {"type": "input_audio", "input_audio": {
                "data": base64.b64encode(wav_bytes).decode(),
                "format": "wav",
            }},
        ]}],
        "temperature": 0.0,
        "max_tokens": 256,
    }
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(f"{BASE_URL}/chat/completions", json=body)
        resp.raise_for_status()
        out = resp.json()
    ms = int((time.monotonic() - started) * 1000)

    raw = (out["choices"][0]["message"].get("content") or "").strip()
    src_name = LANG_NAME.get(source, LANG_NAME[DEFAULT_SOURCE])
    parsed = parse_hearing(raw, src_name=src_name, dst_name="English")
    return {
        "text": parsed["text"],
        "source": parsed["source"],
        "language": parsed["language"],
        "ms": ms,
    }

