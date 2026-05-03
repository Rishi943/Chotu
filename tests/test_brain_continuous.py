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
