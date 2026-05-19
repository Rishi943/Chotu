"""Picker — one LLM call that chooses Chotu's next state + habit.

Spec: docs/superpowers/specs/2026-05-19-picker-design.md
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Literal

from core.llm_client import LLMClient

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
