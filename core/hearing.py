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
import math
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

# Below this RMS the audio is treated as silence and the model is never called.
#
# Taken VERBATIM from E:/AI/gemma-translator/backend/gemma_stt.py's SILENCE_DBFS
# (default -50 dBFS, env STT_SILENCE_DBFS). That module measured its clips on
# 2026-08-07: a good take was -35 dBFS mean, a take where the mic caught almost
# nothing was -67 dBFS, pure silence is -inf. -50 sits in the gap with room on
# both sides. Reusing the same number and units keeps the two apps gating on the
# same notion of "silence".
#
# The gate is not an optimisation, it is a correctness fix. Asked to transcribe
# digital silence the model does not return nothing -- it answers "ठीक आहे" or
# "और" (gemma_stt.py's measured 2026-08-07 observations), and on the console it
# echoed its own prompt scaffolding ("', then the translation in English.
# English:"). A recogniser that returns its own prompt as a result is worse than
# one that returns nothing, because it looks like a real answer.
SILENCE_DBFS = float(os.getenv("PALIV_SILENCE_DBFS", "-50"))


def _pcm_samples(pcm_bytes):
    """Unpack a raw little-endian float32 PCM buffer into a plain tuple of floats."""
    n = len(pcm_bytes) // 4
    return struct.unpack("<%df" % n, pcm_bytes[: n * 4])


def dbfs(samples):
    """Mean level of float32 samples in dBFS. -inf for digital silence.

    Same math as gemma_stt.py's dbfs() (mean-square root over squared samples,
    20*log10), done with stdlib so this module stays numpy-free like the rest of
    core/hearing.py. A -inf here only comes from a zero-length or all-zero
    buffer.
    """
    if not samples:
        return float("-inf")
    n = len(samples)
    acc = 0.0
    for s in samples:
        acc += s * s
    rms = (acc / n) ** 0.5
    if rms <= 0.0:
        return float("-inf")
    return 20.0 * math.log10(rms)


def audio_is_silent(pcm_bytes, threshold=SILENCE_DBFS):
    """True when raw float32 PCM is at/below `threshold` dBFS (default -50)."""
    return dbfs(_pcm_samples(pcm_bytes)) < threshold


# Fragments of this module's own `_PROMPT` scaffolding, used verbatim. When the
# model echoes the prompt instead of answering (which is what happens on
# silence), its reply contains this exact English meta-instruction text. Narrow
# by construction: these are task-format instructions, not generic phrases, so
# a real transcription of speech never trips this. If you change `_PROMPT`,
# keep these in sync.
ECHO_FRAGMENTS = (
    "Transcribe the following speech segment in",
    "then output the string",
    "then the translation in English",
)


def is_prompt_echo(reply):
    """True if a model reply looks like the prompt scaffolding, not content."""
    reply = reply or ""
    return any(frag in reply for frag in ECHO_FRAGMENTS)


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
    src_name = LANG_NAME.get(source, LANG_NAME[DEFAULT_SOURCE])

    # Gate 1 -- energy. Silent/near-silent audio never reaches the model. This is
    # the server-side backstop for Layer 1 (the browser should already have
    # dropped such a take), because layer 1 can be bypassed by a different client
    # or a truncated upload. Same threshold as gemma_stt.py (SILENCE_DBFS).
    if audio_is_silent(audio_bytes):
        return {
            "text": "", "source": "", "language": src_name,
            "ms": 0, "silence": True,
        }

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

    # Gate 2 -- do not trust the model's output either. If the reply is the
    # prompt scaffolding echoed back rather than a transcription, present
    # nothing. A recogniser that returns its own prompt as a result is worse
    # than one that returns nothing.
    if is_prompt_echo(raw):
        return {
            "text": "", "source": "", "language": src_name,
            "ms": ms, "silence": True,
        }

    parsed = parse_hearing(raw, src_name=src_name, dst_name="English")
    return {
        "text": parsed["text"],
        "source": parsed["source"],
        "language": parsed["language"],
        "ms": ms,
        "silence": False,
    }

