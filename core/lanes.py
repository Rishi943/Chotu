"""One call per turn. One JSON object back: what the body does, the face, the line.

Replaced the two-call design on 2026-08-09, which had itself replaced the two
lanes that morning. Rushi: *"simple and small is better as the model is also
simple and small"*. The shape is borrowed from the Sesame companion app
(dorianborian/sesame-companion-app, Apache 2.0), which runs it on cloud Gemini;
the surprise is that it holds on a 2B model running locally.

WHY NOT TOOL CALLING. A tool call and spoken words are two different output
channels, and this model commits to one per turn. Measured over a nine-turn
conversation: the more conversational history the call carried, the more it
narrated `move forward, two steps` as a sentence instead of calling the tool --
tool accuracy 23/30 history-free, 7/30 with history, in exact opposition to how
often it spoke. Two fields of one JSON object are the SAME channel, so there is
nothing to compete, and history stops mattering because every assistant turn in
it now has identical shape.

WHAT IT SCORES. Twenty-four turns covering every tool, every numeric argument,
back-references, refusals, vagueness, and results and events arriving as input;
five replays, 120 turns:

    valid JSON 120/120   right tool 120/120   right arguments 70/70
    spoke 120/120        valid face 120/120   median 0.55 s

Sequences are 40/40: "two steps forward, then two back, and then take one left"
comes back as three ordered actions in one reply.

THE QUEUE STAYS HERE, NOT ON THE PI. The reply can carry several actions, and
they run in order in this process, with the emergency stop checked between each
one. Handing the list to the bridge would have been simpler and would have made
the sequence unstoppable once sent -- and would have needed a new endpoint on a
bridge whose deployed copy and repo copy have diverged.
"""

from __future__ import annotations

import json
import os
import re

from core.dispatch import dispatch_tool
from core.tool_schemas import ACT_NAMES, FACES, MAX_SEQUENCE, SENSE_KINDS

# A spoken line and a short action list. The old design ran uncapped and read its
# own reasoning aloud; 220 tokens is a line plus a full sequence.
MAX_TOKENS = int(os.getenv("PALIV_MAX_TOKENS", "220"))

# llama-server defaults to temperature 1.0 with a random seed, which is why
# "it worked a minute ago" kept not reproducing. 0.3 measured best.
TEMPERATURE = float(os.getenv("PALIV_TEMPERATURE", "0.3"))

# llama-server's response_format. Off costs nothing on the easy cases but was
# worth 114/120 -> 120/120 on the hard set, so it is on by default.
CONSTRAIN = os.getenv("PALIV_CONSTRAIN_JSON", "1") != "0"

DEFAULT_FACE = "idle"

# One item of the action list. `args` is left open because each tool's arguments
# differ; the dispatch layer validates them, as it always did.
STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": ["move", "act", "sense", "read"]},
        "args": {"type": "object"},
    },
    "required": ["tool", "args"],
}

TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "do": {"type": "array", "items": STEP_SCHEMA, "maxItems": MAX_SEQUENCE},
        "face": {"type": "string", "enum": list(FACES)},
        "say": {"type": "string"},
    },
    "required": ["do", "face", "say"],
}


def response_format() -> dict:
    """The kwarg that pins the reply to TURN_SCHEMA."""
    return {"type": "json_schema",
            "json_schema": {"name": "turn", "schema": TURN_SCHEMA, "strict": True}}


# --- reading the reply -----------------------------------------------------

_QUOTE_PAIRS = {
    ord('"'): ord('"'), ord("'"): ord("'"),
    ord("“"): ord("”"), ord("”"): ord("“"),
    ord("‘"): ord("’"), ord("’"): ord("‘"),
}


def strip_wrapping_quotes(line: str) -> str:
    """Remove a matching pair of quotes wrapping the WHOLE line. Quotes inside a
    sentence are left alone."""
    if len(line) < 2:
        return line
    expected = _QUOTE_PAIRS.get(ord(line[0]))
    if expected is not None and ord(line[-1]) == expected:
        return line[1:-1].strip()
    return line


def collapse_repeats(line: str) -> str:
    """Drop an immediately repeated sentence. A guard, not the fix -- capping the
    generation is the fix -- but the 08-09 log shows this model will say
    "Low margin." twelve times when it runs long, and once aloud is enough."""
    out: list[str] = []
    buf = ""
    for ch in line:
        buf += ch
        if ch in ".!?":
            s = buf.strip()
            if s and (not out or out[-1].strip().lower() != s.lower()):
                out.append(s)
            buf = ""
    tail = buf.strip()
    if tail and (not out or out[-1].strip().lower() != tail.lower()):
        out.append(tail)
    return " ".join(out)


def clean_line(text: str) -> str:
    """The spoken line as it will reach the speech engine."""
    return collapse_repeats(strip_wrapping_quotes((text or "").strip()))


def parse_turn(content: str) -> tuple[list[dict], str, str, bool]:
    """(steps, face, line, parsed_ok) out of the model's reply.

    Tolerant on purpose: the schema constraint can be switched off, and a reply
    with prose wrapped around the object should still work rather than losing
    the turn.
    """
    match = re.search(r"\{.*\}", content or "", re.S)
    if not match:
        return [], DEFAULT_FACE, clean_line(content), False
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return [], DEFAULT_FACE, clean_line(content), False
    if not isinstance(data, dict):
        return [], DEFAULT_FACE, clean_line(content), False

    steps = []
    for item in (data.get("do") or [])[:MAX_SEQUENCE]:
        if not isinstance(item, dict):
            continue
        name = item.get("tool")
        if not isinstance(name, str) or not name:
            continue
        args = item.get("args")
        steps.append({"tool": name, "args": args if isinstance(args, dict) else {}})

    face = data.get("face")
    if not isinstance(face, str) or face not in FACES:
        face = DEFAULT_FACE

    return steps, face, clean_line(str(data.get("say") or "")), True


# --- the prompt ------------------------------------------------------------

def system_prompt(chotu_md: str) -> str:
    """CHOTU.md IS the whole prompt. The answer format and the worked examples
    live in it, because they are the part Rushi tunes.

    The lists it names -- the actions, the faces, the sense kinds -- are checked
    against the schemas by `tests/test_lanes.py` rather than generated here, so
    the file stays readable and still cannot drift.
    """
    return chotu_md.strip()


def strip_internal_fields(messages: list[dict]) -> list[dict]:
    """Drop bookkeeping keys before the wire; llama-server passes unknown message
    keys to the chat template."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


def turn_messages(chotu_md: str, memory: list[dict]) -> list[dict]:
    """[system] + the conversation. `memory` already holds the current turn.

    History is NOT trimmed. Google's own chat guidance accumulates it, and with
    every assistant turn now being the same JSON shape there is no pattern in it
    for the model to drift towards. The real ceiling is the server's `-c 4096`,
    which truncates from the FRONT on overflow and would silently eat the
    persona -- so every turn reports its prompt token count.
    """
    return [{"role": "system", "content": system_prompt(chotu_md)},
            *strip_internal_fields(memory)]


def _usage_note(response) -> dict | None:
    u = getattr(response, "usage", None)
    if not u:
        return None
    return {"prompt_tokens": u.get("prompt_tokens"),
            "completion_tokens": u.get("completion_tokens")}


def result_line(tool: str, args: dict, outcome: dict) -> str:
    """One line naming what a tool returned, in the shape the prompt's examples
    use, so the next turn can speak the real numbers."""
    if not isinstance(outcome, dict):
        return f"[result] {tool} {json.dumps(args)} -> {{}}"
    # The error wins when there is one: a dispatch that refuses a move still
    # returns a payload, and reporting "started" for a refused move would be a
    # lie he then says out loud.
    error = outcome.get("error")
    failed = error or not outcome.get("ok")
    if failed:
        body = json.dumps({"error": error or "failed"})
    else:
        body = json.dumps(outcome.get("result") or {"ok": True})
    return f"[result] {tool} {json.dumps(args)} -> {body}"


# --- the turn --------------------------------------------------------------

async def run_turn(llm, dispatch: dict, chotu_md: str,
                   memory: list[dict], text: str | None,
                   tools=None) -> dict:
    """One utterance in; one line, one face and up to `MAX_SEQUENCE` actions out.

    `memory` already includes the current user turn. `text` and `tools` are
    accepted for call compatibility: the turn is read out of memory the way a
    chat client does it, and there are no tool schemas on this path.

    The actions run IN ORDER, here, not on the Pi. Each one is dispatched and
    waited on; if one fails the rest are abandoned, which is what makes the
    emergency stop still work -- `dispatch` refuses `move` and `act` while
    stopped, so a stop between items ends the sequence.

    Returns {"new", "outcomes", "line", "face", "steps", "usage", "parsed"}.
    """
    _ = text, tools

    kwargs = {"max_tokens": MAX_TOKENS, "temperature": TEMPERATURE}
    if CONSTRAIN:
        kwargs["response_format"] = response_format()
    resp = await llm.chat_complete(turn_messages(chotu_md, memory), None, **kwargs)

    content = ""
    if getattr(resp, "choices", None):
        content = resp.choices[0].message.content or ""
    steps, face, line, parsed = parse_turn(content)

    new: list[dict] = []
    outcomes: list[dict] = []

    # His own turn goes back verbatim, as the same JSON he produced. Uniform
    # shape is the point: there is no prose-versus-tool-call pattern in history
    # for him to drift towards, which is what made the old designs rot.
    new.append({"role": "assistant", "_origin": "loop", "content": json.dumps(
        {"do": steps, "face": face, "say": line}, ensure_ascii=False)})

    # Results that deserve a reply. A `sense` is worthless unless he says the
    # number, and a failure is worth remarking on -- both come back as ordinary
    # input, take their own turn, and are answered like anything else. That is
    # what the `[result] ...` examples in CHOTU.md are teaching. A successful
    # move needs none: the motion_done event already arrives on its own.
    replies: list[str] = []

    for step in steps:
        tool, args = step["tool"], step["args"]
        outcome = await dispatch_tool(dispatch, tool, json.dumps(args))
        outcomes.append({"name": tool, "args": args, "result": outcome})
        ok = isinstance(outcome, dict) and outcome.get("ok") and not outcome.get("error")
        if tool in ("sense", "read") or not ok:
            replies.append(result_line(tool, args, outcome))
        if not ok:
            break  # a refused or failed step ends the sequence

    return {"new": new, "outcomes": outcomes, "line": line, "face": face,
            "steps": steps, "replies": replies,
            "usage": _usage_note(resp), "parsed": parsed}
