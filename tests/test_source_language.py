"""The source language is an explicit user choice and must reach the prompt.

Regression for 2026-08-08: asked to detect the language from the audio itself,
Gemma calls Marathi "Hindi" 3 runs out of 3, so detection is dead. Instead the
human names the source language and the prompt spells it out. These tests prove
the chosen language actually gets into the prompt -- and that the default (an
old client sending nothing) is Marathi. The network is intercepted so nothing
leaves the machine.
"""
import asyncio

from core import hearing


class _FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": "x\nEnglish: do it"}}]}


class _FakeClient:
    def __init__(self):
        self.captured = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        self.captured = json
        return _FakeResponse()


def _run_hear(fake, **kw):
    hearing.httpx.AsyncClient = lambda **ignored: fake
    return asyncio.run(hearing.hear(b"audio", "audio/wav", **kw))


def _prompt_sent(fake):
    return fake.captured["messages"][0]["content"][0]["text"]


def test_japanese_source_named_in_prompt():
    fake = _FakeClient()
    out = _run_hear(fake, source="ja")
    assert "Japanese" in _prompt_sent(fake)
    assert out["language"] == "Japanese"


def test_default_source_is_marathi_when_client_sends_nothing():
    fake = _FakeClient()
    _run_hear(fake)  # no source supplied, as an old client would
    assert "Marathi" in _prompt_sent(fake)


def test_unknown_source_falls_back_to_marathi():
    fake = _FakeClient()
    _run_hear(fake, source="zz")
    assert "Marathi" in _prompt_sent(fake)
