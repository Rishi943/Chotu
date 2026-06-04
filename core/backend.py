"""Backend protocol and event types for the live-brain pivot.

A Backend abstracts the LLM transport. Stateless mode uses LlamaServerBackend
(one request/response per turn), live mode uses GeminiLiveBackend (persistent
WebSocket, frames pushed continuously). The brain loop runs two tasks:
a producer that feeds the backend (text + frames + tool results) and a
consumer that drains backend.events() and dispatches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, Union


# --- Event types ---

@dataclass
class ToolCall:
    """Model wants to invoke a tool. The brain dispatches and replies via send_tool_result."""
    id: str
    name: str
    args: dict


@dataclass
class AssistantText:
    """Inner-monologue text from the model. Shown in transcript, not spoken aloud
    (speech is a separate `speak` tool call)."""
    text: str


@dataclass
class SessionEnded:
    """Backend's session has closed cleanly. Reason is human-readable."""
    reason: str


@dataclass
class BackendError:
    """Backend raised an unrecoverable error. The brain loop should stop in v1."""
    message: str
    recoverable: bool = False


Event = Union[ToolCall, AssistantText, SessionEnded, BackendError]


# --- Backend protocol ---

class Backend(Protocol):
    """All LLM backends implement this shape. Async-event-streaming by design;
    stateless backends adapt up to it."""

    async def start(self) -> None:
        """Open whatever connection is needed. May be a no-op for stateless backends."""
        ...

    async def send_user_text(self, text: str) -> None:
        """Push a user-role text turn into the model's context."""
        ...

    async def send_frame(self, jpeg_bytes: bytes, ts: float) -> None:
        """Push one JPEG frame. ts is the laptop monotonic timestamp at capture."""
        ...

    async def send_tool_result(self, tool_call_id: str, result: dict) -> None:
        """Reply to a ToolCall the model previously emitted. result is the Pi envelope."""
        ...

    async def events(self) -> AsyncIterator[Event]:
        """Stream events from the model. Brain's consumer task drains this."""
        ...

    async def close(self) -> None:
        """Tear down. Idempotent."""
        ...
