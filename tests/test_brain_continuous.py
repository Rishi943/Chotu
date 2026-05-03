"""Tests for continuous conversation mode in brain.py."""

import asyncio
import pytest


@pytest.mark.asyncio
async def test_tts_done_event_set_after_speak(monkeypatch):
    """_fire_speak_if_content sets tts_done_event after local_speak completes."""
    import chotu.brain as brain

    brain.tts_done_event.clear()
    brain._pending_speaks = 0

    async def fake_local_speak(text, face_pi=None):
        pass

    monkeypatch.setattr("chotu.tools.local_speak", fake_local_speak)
    monkeypatch.setattr(brain, "MUTE", False)
    monkeypatch.setattr(brain, "_pi_reachable", False)

    task = brain._fire_speak_if_content("hello")
    assert task is not None
    await task
    assert brain.tts_done_event.is_set()
    assert brain._pending_speaks == 0


@pytest.mark.asyncio
async def test_tts_done_event_set_immediately_when_muted(monkeypatch):
    """In mute mode, tts_done_event is set immediately (no speak task)."""
    import chotu.brain as brain

    brain.tts_done_event.clear()
    brain._pending_speaks = 0
    monkeypatch.setattr(brain, "MUTE", True)

    result = brain._fire_speak_if_content("hello")
    assert result is None
    assert brain.tts_done_event.is_set()


@pytest.mark.asyncio
async def test_tts_done_event_set_after_all_speaks(monkeypatch):
    """With two speaks, event is only set after BOTH finish."""
    import chotu.brain as brain

    brain.tts_done_event.clear()
    brain._pending_speaks = 0

    async def fake_local_speak(text, face_pi=None):
        await asyncio.sleep(0.01)

    monkeypatch.setattr("chotu.tools.local_speak", fake_local_speak)
    monkeypatch.setattr(brain, "MUTE", False)
    monkeypatch.setattr(brain, "_pi_reachable", False)

    t1 = brain._fire_speak_if_content("first")
    t2 = brain._fire_speak_if_content("second")
    assert brain._pending_speaks == 2
    assert not brain.tts_done_event.is_set()

    await asyncio.gather(t1, t2)
    assert brain.tts_done_event.is_set()
    assert brain._pending_speaks == 0


@pytest.mark.asyncio
async def test_tts_done_event_not_set_for_empty_content(monkeypatch):
    """Empty content does not touch tts_done_event."""
    import chotu.brain as brain

    brain.tts_done_event.clear()
    brain._pending_speaks = 0
    monkeypatch.setattr(brain, "MUTE", False)

    result = brain._fire_speak_if_content("")
    assert result is None
    assert not brain.tts_done_event.is_set()


@pytest.mark.asyncio
async def test_voice_loop_wake_word_mode_pushes_to_queue(monkeypatch):
    """In wake-word mode (continuous_mode=False), voice_loop uses wait_wake_word then record."""
    import chotu.brain as brain

    calls = []

    class FakeListener:
        def start(self): pass
        def stop(self): pass
        def drain(self): calls.append("drain")
        def wait_wake_word(self):
            calls.append("wait_wake_word")
            return True
        def record_utterance(self):
            calls.append("record_utterance")
            return "walk forward"

    import chotu.voice as v
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

    assert "wait_wake_word" in calls
    assert "drain" in calls
    assert brain.input_queue.get_nowait() == "walk forward"


@pytest.mark.asyncio
async def test_voice_loop_continuous_mode_skips_wake_word(monkeypatch):
    """In continuous mode, voice_loop skips wait_wake_word and awaits tts_done_event."""
    import chotu.brain as brain

    calls = []

    class FakeListener:
        def start(self): pass
        def stop(self): pass
        def drain(self): calls.append("drain")
        def wait_wake_word(self): calls.append("wait_wake_word"); return True
        def record_utterance(self):
            calls.append("record_utterance")
            return "tell me a joke"

    import chotu.voice as v
    monkeypatch.setattr(v, "VoiceListener", FakeListener)
    monkeypatch.setattr(brain, "continuous_mode", True)
    monkeypatch.setattr(brain, "input_queue", asyncio.Queue())
    brain.tts_done_event.set()

    task = asyncio.create_task(brain.voice_loop())
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert "wait_wake_word" not in calls
    assert "drain" in calls
    assert brain.input_queue.get_nowait() == "tell me a joke"


@pytest.mark.asyncio
async def test_voice_loop_continuous_timeout_drops_to_wake_word(monkeypatch):
    """After CONTINUOUS_SILENCE_TIMEOUT with no speech, continuous_mode → False."""
    import chotu.brain as brain
    import chotu.voice as v

    calls = []

    class FakeListener:
        def start(self): pass
        def stop(self): pass
        def drain(self): pass
        def wait_wake_word(self):
            calls.append("wait_wake_word")
            return True
        def record_utterance(self):
            calls.append("record_utterance")
            return ""  # no speech

    monkeypatch.setattr(v, "VoiceListener", FakeListener)
    monkeypatch.setattr(brain, "continuous_mode", True)
    monkeypatch.setattr(brain, "input_queue", asyncio.Queue())
    monkeypatch.setattr(v, "CONTINUOUS_SILENCE_TIMEOUT", 0)  # instant timeout
    brain.tts_done_event.set()

    task = asyncio.create_task(brain.voice_loop())
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert brain.continuous_mode is False
    assert "wait_wake_word" in calls  # dropped back to wake-word path
