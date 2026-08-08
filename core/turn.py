"""One exchange: keep calling the model until it stops asking for tools.

Paliv's old loop called the model exactly once per turn, so a tool result was
not read until the NEXT turn -- asking for the battery got an answer one turn
late. This is the fix, and it is the whole reason the fork exists.
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable

from core.dispatch import dispatch_tool

MAX_RESULT_CHARS = 1500


def _cap(text: str) -> str:
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "…"


async def run_turn(
    llm,
    dispatch: dict,
    messages: list[dict],
    *,
    tools=None,
    max_rounds: int = 6,
    on_event: Callable[[str, dict], None] | None = None,
) -> list[dict]:
    """Run one exchange to completion. Returns only the NEW messages."""
    if tools is None:
        from core.tool_schemas import TOOL_SCHEMAS
        tools = TOOL_SCHEMAS

    def emit(kind: str, payload: dict) -> None:
        if on_event is not None:
            try:
                on_event(kind, payload)
            except Exception:
                pass

    convo = list(messages)
    new: list[dict] = []

    for _ in range(max_rounds):
        response = await llm.chat_complete(convo, tools)
        if not response.choices:
            break
        assistant = llm.format_assistant_message(response)
        convo.append(assistant)
        new.append(assistant)
        emit("assistant", {"content": assistant.get("content")})

        calls = response.choices[0].message.tool_calls or []
        if not calls:
            break

        for c in calls:
            emit("tool_call", {"name": c.function.name,
                               "arguments": c.function.arguments})

        envs = await asyncio.gather(*[
            dispatch_tool(dispatch, c.function.name, c.function.arguments)
            for c in calls
        ])

        for c, env in zip(calls, envs):
            emit("tool_result", {"name": c.function.name, "ok": env["ok"],
                                 "error": env.get("error")})
            msg = llm.format_tool_result(c.id, _cap(json.dumps(env)))
            convo.append(msg)
            new.append(msg)

    return new
