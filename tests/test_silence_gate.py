"""Silence gate + prompt-echo guard for the hearing path. No network.

Proves the server refuses to call the model on silent / near-silent audio and
that a model reply which is the prompt scaffolding is not presented as content.
Zero writes: every buffer here is synthetic.
"""
import io
import struct
import wave

from core import hearing


def _pcm(levels):
    """Raw little-endian float32 PCM buffer from a list of amplitudes."""
    return struct.pack("<%df" % len(levels), *levels)


def _silent_pcm(seconds=1.0, rate=16000):
    return _pcm([0.0] * int(seconds * rate))


def _tone_pcm(dbfs, seconds=1.0, rate=16000):
    """A constant-amplitude tone at a target dBFS (positive amplitude)."""
    amp = 10 ** (dbfs / 20.0)
    return _pcm([amp] * int(seconds * rate))


def test_digital_silence_is_gated():
    assert hearing.audio_is_silent(_silent_pcm()) is True


def test_near_silence_is_gated():
    # The near-silent take gemma_stt.py measured was -67 dBFS; well under -50.
    assert hearing.audio_is_silent(_tone_pcm(-67)) is True


def test_loud_speech_is_not_gated():
    # gemma_stt.py measured a good take at -35 dBFS; well above the -50 gate.
    assert hearing.audio_is_silent(_tone_pcm(-35)) is False


def test_hear_returns_empty_without_calling_the_model_on_silence(monkeypatch):
    called = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            called["n"] += 1
            raise AssertionError("model must not be called on silence")

    monkeypatch.setattr(hearing.httpx, "AsyncClient", FakeClient)

    import asyncio
    result = asyncio.run(
        hearing.hear(_silent_pcm(), "application/octet-stream", source="mr")
    )
    assert result["text"] == ""
    assert result["source"] == ""
    assert result["silence"] is True
    assert called["n"] == 0


def test_observed_prompt_leak_is_flagged():
    # The exact leak Rushi saw on the console when silence was sent.
    leaked = "', then the translation in English. English:"
    assert hearing.is_prompt_echo(leaked) is True


def test_real_transcription_is_not_flagged():
    # A genuine two-part answer must never be suppressed.
    good = (
        "छोटू काका ना डान्स करून दाखव\n"
        "English: Chhotu Kaka, show us a dance."
    )
    assert hearing.is_prompt_echo(good) is False


def _wav_to_pcm_bytes(path):
    """Read a 16-bit WAV file into raw float32 PCM bytes (for the ref clip)."""
    with wave.open(path, "rb") as w:
        nframes = w.getnframes()
        frames = w.readframes(nframes)
    vals = struct.unpack("<%dh" % nframes, frames)
    return struct.pack("<%df" % nframes, *[v / 32768.0 for v in vals])


def test_reference_marathi_speech_passes_the_gate():
    """READ ONLY -- the real reference clip must get through the energy gate."""
    # Guarded so the suite still runs if the scratch clip is ever pruned.
    path = r"E:\AI\picrawler-vfx\renders\_scratch\voice\ref_mr.wav"
    try:
        pcm = _wav_to_pcm_bytes(path)
    except FileNotFoundError:
        import pytest
        pytest.skip("reference clip not present")
    assert hearing.audio_is_silent(pcm) is False