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

import base64
import os
import time

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
    """Split the model's reply into {"text", "language"}. Never raises.

    text is the English translation (what the brain consumes). language is the
    source-language name we already knew going in -- the model no longer names
    it, so we label the reply with the user's explicit choice.
    """
    reply = (reply or "").strip()
    if not reply:
        return {"text": "", "language": src_name}

    marker = dst_name + ":"
    if marker in reply:
        _, _, translation = reply.partition(marker)
        return {"text": translation.strip(), "language": src_name}

    lines = [l.strip() for l in reply.splitlines() if l.strip()]
    if len(lines) >= 2:
        return {"text": " ".join(lines[1:]), "language": src_name}
    return {"text": reply, "language": src_name}


async def hear(audio_bytes, mime, source=DEFAULT_SOURCE):
    """POST audio to llama-server (E2B, :8099) and return {"text","language","ms"}.

    Request shape copied from gemma_stt.py's `_chat`: a chat-completion with an
    `input_audio` content block carrying base64 WAV bytes. `mime` is accepted
    for the route's own bookkeeping but the audio is always sent as `wav`.

    The source language is the user's explicit choice (default Marathi); the
    prompt names it, so the model never has to guess.
    """
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": build_prompt(source)},
            {"type": "input_audio", "input_audio": {
                "data": base64.b64encode(audio_bytes).decode(),
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
    return {"text": parsed["text"], "language": parsed["language"], "ms": ms}

