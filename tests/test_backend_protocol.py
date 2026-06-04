"""Sanity tests for the Backend Event dataclasses."""
from core.backend import ToolCall, AssistantText, SessionEnded, BackendError, Event


def test_toolcall_fields():
    tc = ToolCall(id="fc-1", name="speak", args={"text": "hi"})
    assert tc.id == "fc-1"
    assert tc.name == "speak"
    assert tc.args == {"text": "hi"}


def test_assistant_text_fields():
    at = AssistantText(text="kitchen on it")
    assert at.text == "kitchen on it"


def test_session_ended_is_event():
    assert isinstance(SessionEnded(reason="goaway"), Event)


def test_backend_error_carries_message():
    e = BackendError(message="ws closed", recoverable=False)
    assert "ws closed" in e.message
    assert e.recoverable is False
