import asyncio
import core.voice as voice


class _FakeListener:
    """Records call order; returns a canned utterance from record_utterance."""
    instances = []

    def __init__(self):
        self.calls = []
        _FakeListener.instances.append(self)

    def start(self):
        self.calls.append("start")

    def drain(self):
        self.calls.append("drain")

    def record_utterance(self):
        self.calls.append("record")
        return "walk forward"

    def stop(self):
        self.calls.append("stop")


def test_record_push_to_talk_drains_records_stops(monkeypatch):
    _FakeListener.instances = []
    monkeypatch.setattr(voice, "VoiceListener", _FakeListener)
    text = asyncio.run(voice.record_push_to_talk())
    assert text == "walk forward"
    listener = _FakeListener.instances[0]
    # opens, drains stale audio, records, always closes — in that order
    assert listener.calls == ["start", "drain", "record", "stop"]


import core.brain as brain


def _drain_events():
    states = []
    while True:
        try:
            states.append(brain.gui_event_queue.get_nowait())
        except Exception:
            break
    return [e.get("state") for e in states if e.get("type") == "ptt"]


def _reset_brain():
    brain._ptt_capturing = False
    brain.handsfree_task = None
    brain.pending_input.drain()
    _drain_events()


def test_trigger_ptt_capture_pushes_text_and_brackets_events(monkeypatch):
    _reset_brain()

    async def _fake():
        return "look left"
    monkeypatch.setattr("core.voice.record_push_to_talk", _fake)

    asyncio.run(brain.trigger_ptt_capture())
    assert brain.pending_input.drain() == "look left"
    assert _drain_events() == ["recording", "idle"]


def test_trigger_ptt_capture_no_push_on_empty(monkeypatch):
    _reset_brain()

    async def _fake():
        return "   "
    monkeypatch.setattr("core.voice.record_push_to_talk", _fake)

    asyncio.run(brain.trigger_ptt_capture())
    assert brain.pending_input.drain() is None


def test_trigger_ptt_capture_single_flight_when_already_capturing(monkeypatch):
    _reset_brain()
    brain._ptt_capturing = True  # a capture is "in progress"

    async def _fake():
        return "should not run"
    monkeypatch.setattr("core.voice.record_push_to_talk", _fake)

    asyncio.run(brain.trigger_ptt_capture())
    assert brain.pending_input.drain() is None


def test_trigger_ptt_capture_ignored_when_handsfree_active(monkeypatch):
    _reset_brain()
    brain.handsfree_task = object()  # hands-free loop "running"

    async def _fake():
        return "should not run"
    monkeypatch.setattr("core.voice.record_push_to_talk", _fake)

    asyncio.run(brain.trigger_ptt_capture())
    assert brain.pending_input.drain() is None
    brain.handsfree_task = None
