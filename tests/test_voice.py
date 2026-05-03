"""Tests for chotu/voice.py — pure audio utilities."""

import numpy as np
import pytest


def test_audio_to_int16_range():
    from chotu.voice import _audio_to_int16
    # float32 in [-1, 1] should map to int16 range
    chunk = np.array([0.0, 1.0, -1.0, 0.5], dtype=np.float32)
    result = _audio_to_int16(chunk)
    assert result.dtype == np.int16
    assert result[0] == 0
    assert result[1] == 32767
    assert result[2] == -32768
    assert 16383 <= result[3] <= 16384


def test_audio_to_int16_clips_over_range():
    from chotu.voice import _audio_to_int16
    chunk = np.array([1.5, -1.5], dtype=np.float32)
    result = _audio_to_int16(chunk)
    assert result.dtype == np.int16
    assert result[0] == 32767
    assert result[1] == -32768


def test_is_speech_loud_chunk():
    from chotu.voice import _is_speech
    # 0.5 amplitude = RMS 0.5, well above default 0.01 threshold
    loud = np.full(1280, 0.5, dtype=np.float32)
    assert _is_speech(loud) is True


def test_is_speech_silent_chunk():
    from chotu.voice import _is_speech
    silent = np.zeros(1280, dtype=np.float32)
    assert _is_speech(silent) is False


def test_is_speech_custom_threshold():
    from chotu.voice import _is_speech
    chunk = np.full(1280, 0.05, dtype=np.float32)
    assert _is_speech(chunk, threshold=0.1) is False
    assert _is_speech(chunk, threshold=0.01) is True


def test_is_speech_empty_chunk():
    from chotu.voice import _is_speech
    assert _is_speech(np.array([], dtype=np.float32)) is False


def test_get_whisper_returns_same_instance(monkeypatch):
    """Lazy loader returns the same object on repeated calls."""
    import chotu.voice as v
    v._whisper_model = None  # reset singleton

    fake_model = object()
    monkeypatch.setattr(
        "chotu.voice.WhisperModel",
        lambda *a, **kw: fake_model,
    )
    first = v._get_whisper()
    second = v._get_whisper()
    assert first is second is fake_model


def test_get_oww_returns_same_instance(monkeypatch):
    import chotu.voice as v
    v._oww_model = None

    class FakeModel:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr("chotu.voice.OWWModel", FakeModel)
    first = v._get_oww()
    second = v._get_oww()
    assert first is second


def test_blocking_listen_transcribes(monkeypatch):
    """Full pipeline: fake wake word fires after 3 chunks, then speech + silence."""
    import queue as q
    import chotu.voice as v

    audio_q = q.Queue()
    speech_chunk = np.full(1280, 0.5, dtype=np.float32)
    silent_chunk = np.zeros(1280, dtype=np.float32)

    silence_limit = int(v.SILENCE_TIMEOUT_S * v.SAMPLE_RATE / v.CHUNK_SAMPLES) + 1
    for _ in range(3):
        audio_q.put(silent_chunk.copy())
    audio_q.put(speech_chunk.copy())   # wake word fires here
    for _ in range(5):
        audio_q.put(speech_chunk.copy())
    for _ in range(silence_limit + 2):
        audio_q.put(silent_chunk.copy())

    class FakeStream:
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr("sounddevice.InputStream", lambda **kw: FakeStream())
    monkeypatch.setattr("chotu.voice.queue.Queue", lambda: audio_q)

    call_count = {"n": 0}

    class FakeOWW:
        def reset(self): pass
        def predict(self, chunk):
            call_count["n"] += 1
            score = 0.9 if call_count["n"] >= 4 else 0.0
            return {"hey_jarvis": score}

    monkeypatch.setattr(v, "_oww_model", FakeOWW())

    class FakeSeg:
        text = " hello chotu"

    class FakeWhisper:
        def transcribe(self, audio, **kw):
            return [FakeSeg()], None

    monkeypatch.setattr(v, "_whisper_model", FakeWhisper())

    result = v._blocking_listen_and_transcribe()
    assert result == "hello chotu"


@pytest.mark.asyncio
async def test_voice_loop_pushes_to_queue(monkeypatch):
    """voice_loop() should push transcribed text into input_queue."""
    import asyncio
    import chotu.brain as brain

    call_count = {"n": 0}

    async def fake_listen():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "walk forward"
        await asyncio.sleep(999)

    monkeypatch.setattr("chotu.brain.listen_and_transcribe", fake_listen)
    monkeypatch.setattr(brain, "input_queue", asyncio.Queue())

    task = asyncio.create_task(brain.voice_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert not brain.input_queue.empty()
    assert brain.input_queue.get_nowait() == "walk forward"


# ── VoiceListener tests ──────────────────────────────────────

class _FakeStream:
    """Minimal sounddevice.InputStream stand-in."""
    def __init__(self, **kw):
        self.started = False
        self.stopped = False
        self.closed = False
    def start(self): self.started = True
    def stop(self): self.stopped = True
    def close(self): self.closed = True


def test_voice_listener_start_opens_stream(monkeypatch):
    import chotu.voice as v
    fake = _FakeStream()
    monkeypatch.setattr("sounddevice.InputStream", lambda **kw: fake)
    listener = v.VoiceListener()
    listener.start()
    assert fake.started


def test_voice_listener_stop_closes_stream(monkeypatch):
    import chotu.voice as v
    fake = _FakeStream()
    monkeypatch.setattr("sounddevice.InputStream", lambda **kw: fake)
    listener = v.VoiceListener()
    listener.start()
    listener.stop()
    assert fake.stopped
    assert fake.closed
    assert listener._stream is None


def test_voice_listener_drain_empties_queue(monkeypatch):
    import queue
    import chotu.voice as v
    monkeypatch.setattr("sounddevice.InputStream", lambda **kw: _FakeStream())
    listener = v.VoiceListener()
    listener.start()
    for i in range(5):
        listener._audio_q.put(np.zeros(1280, dtype=np.float32))
    assert not listener._audio_q.empty()
    listener.drain()
    assert listener._audio_q.empty()
