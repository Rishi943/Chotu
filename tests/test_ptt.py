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


class _HFListener:
    """Fake VoiceListener for the hands-free loop: yields one utterance then ''."""
    def __init__(self):
        self._n = 0

    def start(self): pass
    def stop(self): pass
    def drain(self): pass

    def record_utterance(self):
        self._n += 1
        return "come here" if self._n == 1 else ""


def test_set_handsfree_starts_pushes_and_stops(monkeypatch):
    _reset_brain()
    monkeypatch.setattr("core.voice.VoiceListener", _HFListener)

    async def _run():
        brain.tts_done_event.set()        # allow turn-taking to proceed
        brain.set_handsfree(True)
        assert brain.handsfree_task is not None
        await asyncio.sleep(0.1)           # let one capture happen
        got = brain.pending_input.drain()
        brain.set_handsfree(False)
        await asyncio.sleep(0.05)          # let cancellation + finally run
        return got

    got = asyncio.run(_run())
    assert got == "come here"
    assert brain.handsfree_task is None
    states = _drain_events()
    assert "handsfree_on" in states
    assert states[-1] == "handsfree_off"


def test_set_handsfree_false_is_noop_when_not_running():
    _reset_brain()
    brain.set_handsfree(False)            # must not raise
    assert brain.handsfree_task is None
