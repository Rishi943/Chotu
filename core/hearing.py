"""One-call hearing: audio in, English text + spoken-language name out.

Request shape and the one-call prompt idea are copied from the working path in
`E:/AI/gemma-translator/backend/gemma_stt.py` (`_chat` and `_BOTH`) --
Google's model-card wording for the transcribe-then-translate call. That
module always knows the source language ahead of time (`src="mr"` etc); this
endpoint does not, so the prompt is minimally extended to ask the model to
name the language itself, on its own first line, `Language: <name>`, so
`parse_hearing` has something fixed to split on. Do not touch the transcribe
verbatim wording style — model-card prompts are brittle and every invented
rephrasing so far has made the model transliterate instead of translate.
"""

from __future__ import annotations

import base64
import os
import time

import httpx

BASE_URL = os.getenv("PALIV_BRAIN_URL", "http://127.0.0.1:8099/v1")
MODEL = os.getenv("PALIV_BRAIN_MODEL", "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf")

# Extends gemma_stt.py's _BOTH pattern: same transcribe-then-translate shape,
# but the source language is unknown here, so the model names it itself
# instead of being told. The "Language: " line is new; everything else about
# the one-call structure (transcribe first, then translate) is unchanged.
_PROMPT = (
    "Transcribe the following speech segment, then translate it into English.\n"
    "When formatting the answer, first output the string 'Language: ', then "
    "the name of the language spoken, then one newline, then output the "
    "translation in English."
)


def parse_hearing(reply: str) -> dict:
    """Split the model's raw reply into {"text", "language"}. Never raises."""
    reply = (reply or "").strip()
    if not reply:
        return {"text": "", "language": ""}

    lines = reply.splitlines()
    language = ""
    rest = lines
    first = lines[0].strip()
    if first.lower().startswith("language:"):
        language = first.split(":", 1)[1].strip()
        rest = lines[1:]

    text = "\n".join(rest).strip()
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
        text = text[1:-1].strip()

    return {"text": text, "language": language}


async def hear(audio_bytes: bytes, mime: str) -> dict:
    """POST audio to llama-server (E2B, :8099) and return {"text","language","ms"}.

    Request shape copied from gemma_stt.py's `_chat`: a chat-completion with an
    `input_audio` content block carrying base64 WAV bytes. `mime` is accepted
    for the route's own bookkeeping but the audio is always sent as `wav` --
    the same format gemma_stt.py always uses (it wraps raw PCM in a WAV
    container before calling this shape).
    """
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _PROMPT},
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
    parsed = parse_hearing(raw)
    return {"text": parsed["text"], "language": parsed["language"], "ms": ms}
