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
