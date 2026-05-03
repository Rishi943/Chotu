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


def test_listen_and_transcribe_wrapper(monkeypatch):
    """listen_and_transcribe() must call wait_wake_word, drain, record_utterance, stop."""
    import chotu.voice as v

    calls = []

    class FakeListener:
        def start(self): calls.append("start")
        def stop(self): calls.append("stop")
        def drain(self): calls.append("drain")
        def wait_wake_word(self): calls.append("wait_wake_word"); return True
        def record_utterance(self): calls.append("record_utterance"); return "hello"

    monkeypatch.setattr(v, "VoiceListener", FakeListener)

    result = v._blocking_listen_and_transcribe_via_class()
    assert result == "hello"
    assert calls == ["start", "wait_wake_word", "drain", "record_utterance", "stop"]


@pytest.mark.asyncio
async def test_voice_loop_pushes_to_queue_wake_word_mode(monkeypatch):
    """Regression: wake-word mode voice_loop still pushes transcribed text to input_queue."""
    import asyncio
    import chotu.brain as brain
    import chotu.voice as v

    class FakeListener:
        def start(self): pass
        def stop(self): pass
        def drain(self): pass
        def wait_wake_word(self): return True
        def record_utterance(self): return "walk forward"

    monkeypatch.setattr(v, "VoiceListener", FakeListener)
    monkeypatch.setattr(brain, "continuous_mode", False)
    monkeypatch.setattr(brain, "input_queue", asyncio.Queue())

    task = asyncio.create_task(brain.voice_loop())
    await asyncio.sleep(0.1)
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


def test_voice_listener_wait_wake_word_detects(monkeypatch):
    """wait_wake_word() returns True after OWW threshold is crossed."""
    import chotu.voice as v

    monkeypatch.setattr("sounddevice.InputStream", lambda **kw: _FakeStream())

    call_count = {"n": 0}

    class FakeOWW:
        def reset(self): pass
        def predict(self, chunk):
            call_count["n"] += 1
            return {"hey_jarvis": 0.9 if call_count["n"] >= 3 else 0.0}

    monkeypatch.setattr(v, "_oww_model", FakeOWW())

    listener = v.VoiceListener()
    listener.start()

    # Feed 5 silent chunks — OWW fires at chunk 3
    for _ in range(5):
        listener._audio_q.put(np.zeros(1280, dtype=np.float32))

    result = listener.wait_wake_word()
    assert result is True
    assert call_count["n"] >= 3


def test_voice_listener_wait_wake_word_below_threshold(monkeypatch):
    """wait_wake_word keeps looping while score is below threshold."""
    import chotu.voice as v

    monkeypatch.setattr("sounddevice.InputStream", lambda **kw: _FakeStream())

    call_count = {"n": 0}

    class FakeOWW:
        def reset(self): pass
        def predict(self, chunk):
            call_count["n"] += 1
            # Fire on exactly the 4th chunk
            return {"hey_jarvis": 0.9 if call_count["n"] == 4 else 0.0}

    monkeypatch.setattr(v, "_oww_model", FakeOWW())

    listener = v.VoiceListener()
    listener.start()

    for _ in range(4):
        listener._audio_q.put(np.zeros(1280, dtype=np.float32))

    result = listener.wait_wake_word()
    assert result is True
    assert call_count["n"] == 4


def _make_listener_with_fake_stream(monkeypatch):
    """Helper: return a started VoiceListener with a no-op stream."""
    import chotu.voice as v
    monkeypatch.setattr("sounddevice.InputStream", lambda **kw: _FakeStream())
    listener = v.VoiceListener()
    listener.start()
    return listener


def test_voice_listener_record_utterance_transcribes(monkeypatch):
    import chotu.voice as v
    listener = _make_listener_with_fake_stream(monkeypatch)

    speech_chunk = np.full(1280, 0.5, dtype=np.float32)
    silent_chunk = np.zeros(1280, dtype=np.float32)

    silence_limit = int(v.SILENCE_TIMEOUT_S * v.SAMPLE_RATE / v.CHUNK_SAMPLES) + 2
    for _ in range(5):
        listener._audio_q.put(speech_chunk.copy())
    for _ in range(silence_limit):
        listener._audio_q.put(silent_chunk.copy())

    class FakeSeg:
        text = " hello chotu"

    class FakeWhisper:
        def transcribe(self, audio, **kw):
            return [FakeSeg()], None

    monkeypatch.setattr(v, "_whisper_model", FakeWhisper())

    result = listener.record_utterance()
    assert result == "hello chotu"


def test_voice_listener_record_utterance_empty_if_no_speech(monkeypatch):
    import chotu.voice as v
    listener = _make_listener_with_fake_stream(monkeypatch)

    silent_chunk = np.zeros(1280, dtype=np.float32)
    silence_limit = int(v.SILENCE_TIMEOUT_S * v.SAMPLE_RATE / v.CHUNK_SAMPLES) + 2
    for _ in range(silence_limit):
        listener._audio_q.put(silent_chunk.copy())

    class FakeWhisper:
        def transcribe(self, audio, **kw):
            return [], None

    monkeypatch.setattr(v, "_whisper_model", FakeWhisper())
    result = listener.record_utterance()
    assert result == ""
