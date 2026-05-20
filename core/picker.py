"""Picker — one LLM call that chooses Chotu's next state + habit.

Spec: docs/superpowers/specs/2026-05-19-picker-design.md
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Literal

from core.llm_client import LLMClient, LLMResponse

logger = logging.getLogger(__name__)

State = Literal["idle", "play"]

IDLE_HABITS: list[str] = ["do_nothing", "dangle_paws", "yawn", "look_around", "shake_paw"]
PLAY_HABITS: list[str] = ["explore"]


@dataclass
class PickerInput:
    current_state: State
    recent_picks: list[str] = field(default_factory=list)  # oldest first, len <= 5


@dataclass
class Pick:
    state: State
    name: str


PICK_HABIT_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "pick_habit",
        "description": "Choose Chotu's next state and habit.",
        "parameters": {
            "type": "object",
            "properties": {
                "state": {"type": "string", "enum": ["idle", "play"]},
                "name":  {"type": "string"},
            },
            "required": ["state", "name"],
        },
    },
}


SYSTEM_PROMPT = (
    "You are Chotu's habit picker. Your only job is to choose what Chotu does next.\n\n"
    "You can stay in the current state or transition. Prefer variety over repetition — "
    "if the recent picks list shows the same habit twice in a row, pick something else. "
    "After many IDLE picks in a row, consider transitioning to PLAY.\n\n"
    f"Available IDLE habits: {', '.join(IDLE_HABITS)}.\n"
    f"Available PLAY habits: {', '.join(PLAY_HABITS)}.\n\n"
    "Call the `pick_habit` tool exactly once. Do not speak. Do not call any other tool."
)


FALLBACK_PICK = Pick(state="idle", name="do_nothing")


def _validate(response: LLMResponse) -> Pick:
    """Validate an LLMResponse and return a Pick. Never raises."""
    try:
        msg = response.choices[0].message
    except (IndexError, AttributeError):
        logger.warning("picker fallback: no choices in response")
        return FALLBACK_PICK

    tcs = msg.tool_calls
    if not tcs:
        logger.warning("picker fallback: no tool_calls in response")
        return FALLBACK_PICK

    tc = tcs[0]
    if tc.function.name != "pick_habit":
        logger.warning("picker fallback: wrong tool name=%s", tc.function.name)
        return FALLBACK_PICK

    try:
        args = json.loads(tc.function.arguments or "{}")
    except json.JSONDecodeError:
        logger.warning("picker fallback: arguments not JSON: %r", tc.function.arguments)
        return FALLBACK_PICK

    state = args.get("state")
    name = args.get("name")
    if state not in ("idle", "play"):
        logger.warning("picker fallback: bad state=%r", state)
        return FALLBACK_PICK
    if not isinstance(name, str):
        logger.warning("picker fallback: bad name=%r", name)
        return FALLBACK_PICK

    allowed = IDLE_HABITS if state == "idle" else PLAY_HABITS
    if name not in allowed:
        logger.warning("picker fallback: name=%r not in %s habits", name, state)
        return FALLBACK_PICK

    return Pick(state=state, name=name)


def _render_recent(picks: list[str]) -> str:
    return ", ".join(picks) if picks else "none yet"


def _build_messages(ctx: PickerInput) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Current state: {ctx.current_state}.\n"
                f"Recent picks (oldest → newest): {_render_recent(ctx.recent_picks)}."
            ),
        },
    ]


async def pick_next(ctx: PickerInput, llm: LLMClient) -> Pick:
    """Single picker call. Validated. Never raises."""
    messages = _build_messages(ctx)
    try:
        response = await llm.chat_complete(
            messages=messages,
            tools=[PICK_HABIT_TOOL],
            thinking=True,
            tool_choice={"type": "function", "function": {"name": "pick_habit"}},
            max_tokens=1024,
        )
    except Exception as e:
        logger.warning("picker fallback: LLM call raised %s: %s", type(e).__name__, e)
        return FALLBACK_PICK

    return _validate(response)
