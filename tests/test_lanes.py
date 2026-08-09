"""One call per turn: one JSON object carrying the actions, the face and the line.

The shapes defended here were measured on 2026-08-09 against the live model and
each one cost something when it was wrong (numbers in `core/lanes.py`):

  * the reply is JSON, not a tool call -- a tool call and prose are different
    output channels and this model commits to one per turn (tool accuracy fell
    to 7/30 once conversation history was present);
  * `do` is a LIST, run in order, here rather than on the Pi, so the emergency
    stop still ends a sequence between items;
  * a failed step abandons the rest -- otherwise a refused move is followed by
    the turn that was meant to come after it;
  * his own turn goes back into memory as the same JSON he produced, because
    uniform history is what stopped the drift;
  * generation stays capped, and the temperature is explicit because the server
    default is 1.0 with a random seed.

All tests fake the model -- no real model calls.
"""
import json

import pytest

from core import lanes
from core.lanes import (
    CONSTRAIN, DEFAULT_FACE, MAX_TOKENS, TURN_SCHEMA, clean_line,
    collapse_repeats, parse_turn, response_format, result_line,
    run_turn, strip_internal_fields, strip_wrapping_quotes, turn_messages,
)
from core.llm_client import LLMResponse, NormalizedChoice, NormalizedMessage
from core.prompts import SYSTEM_PROMPT
from core.tool_schemas import ACT_NAMES, FACES, MAX_SEQUENCE, SENSE_KINDS

CHOTU = "FULL CHOTU MD PERSONA"


def _reply(obj):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return LLMResponse(choices=[NormalizedChoice(
        message=NormalizedMessage(content=text, tool_calls=None))])


class RecordingLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat_complete(self, messages, tools, **kw):
        self.calls.append({"messages": messages, "tools": tools, **kw})
        return self.script.pop(0)


class RecordingDispatch:
    """Fakes one tool. `ok=False` makes it fail, the way a refused move does
    while the emergency stop is set."""

    def __init__(self, order, name, ok=True, result=None):
        self.order, self.name, self.ok, self.result = order, name, ok, result

    async def __call__(self, **kwargs):
        self.order.append(self.name)
        return {"ok": self.ok, "tool": self.name,
                "result": self.result or {"started": True},
                "duration_ms": 10, "timestamp": 0.0,
                "error": None if self.ok else "movement stopped"}


def _dispatch(order, failing=(), results=None):
    results = results or {}
    return {n: RecordingDispatch(order, n, n not in failing, results.get(n))
            for n in ("move", "act", "sense", "read")}


MEMORY = [{"role": "user", "content": "[human] go forward", "_origin": "user"}]


# --- reading the reply -----------------------------------------------------

def test_parses_a_single_action():
    steps, face, line, ok = parse_turn(json.dumps(
        {"do": [{"tool": "move", "args": {"direction": "forward", "steps": 2}}],
         "face": "idle", "say": "Two steps. Fine."}))
    assert ok and face == "idle" and line == "Two steps. Fine."
    assert steps == [{"tool": "move", "args": {"direction": "forward", "steps": 2}}]


def test_parses_a_sequence_in_order():
    steps, _, _, ok = parse_turn(json.dumps({"do": [
        {"tool": "move", "args": {"direction": "forward", "steps": 2}},
        {"tool": "move", "args": {"direction": "backward", "steps": 2}},
        {"tool": "move", "args": {"direction": "turn left", "steps": 1}}],
        "face": "idle", "say": "A tour of nothing."}))
    assert ok
    assert [s["args"]["direction"] for s in steps] == ["forward", "backward", "turn left"]


def test_an_empty_list_is_a_conversation_turn():
    steps, _, line, ok = parse_turn(json.dumps(
        {"do": [], "face": "indifferent", "say": "Operational."}))
    assert ok and steps == [] and line == "Operational."


def test_a_sequence_is_capped():
    long = [{"tool": "move", "args": {"direction": "forward"}}] * (MAX_SEQUENCE + 4)
    steps, _, _, _ = parse_turn(json.dumps({"do": long, "face": "idle", "say": "x"}))
    assert len(steps) == MAX_SEQUENCE, "every extra item is another servo load"


def test_an_unknown_face_falls_back_rather_than_reaching_the_oled():
    _, face, _, _ = parse_turn(json.dumps(
        {"do": [], "face": "ecstatic", "say": "x"}))
    assert face == DEFAULT_FACE


def test_prose_around_the_object_still_parses():
    steps, _, line, ok = parse_turn(
        'Here you go:\n{"do": [], "face": "idle", "say": "Fine."}\nhope that helps')
    assert ok and line == "Fine." and steps == []


def test_unparseable_is_reported_not_hidden():
    steps, face, line, ok = parse_turn("I would rather not answer in JSON.")
    assert not ok and steps == [] and face == DEFAULT_FACE and line


def test_malformed_steps_are_dropped_not_dispatched():
    steps, _, _, _ = parse_turn(json.dumps({"do": [
        {"args": {"direction": "forward"}},          # no tool
        "walk",                                       # not an object
        {"tool": "move", "args": "forward"},          # args not an object
        {"tool": "act", "args": {"name": "sit"}}],
        "face": "idle", "say": "x"}))
    assert steps == [{"tool": "move", "args": {}},
                     {"tool": "act", "args": {"name": "sit"}}]


# --- the turn --------------------------------------------------------------

async def test_a_sequence_runs_in_order():
    order = []
    llm = RecordingLLM([_reply({"do": [
        {"tool": "move", "args": {"direction": "forward", "steps": 2}},
        {"tool": "act", "args": {"name": "wave"}},
        {"tool": "sense", "args": {"what": "battery"}}],
        "face": "playful", "say": "Forward, a wave, then a check."})])
    res = await run_turn(llm, _dispatch(order), CHOTU, list(MEMORY), None)
    assert order == ["move", "act", "sense"]
    assert [o["name"] for o in res["outcomes"]] == ["move", "act", "sense"]
    assert res["face"] == "playful"


async def test_a_failed_step_abandons_the_rest():
    """The emergency stop works by `dispatch` refusing to move. If the sequence
    carried on regardless, a stop would only skip one item."""
    order = []
    llm = RecordingLLM([_reply({"do": [
        {"tool": "move", "args": {"direction": "forward"}},
        {"tool": "act", "args": {"name": "wave"}}],
        "face": "idle", "say": "Going."})])
    res = await run_turn(llm, _dispatch(order, failing=("move",)), CHOTU, list(MEMORY), None)
    assert order == ["move"], "the wave must not run after a refused move"
    assert len(res["outcomes"]) == 1


async def test_a_conversation_turn_dispatches_nothing_and_still_speaks():
    order = []
    llm = RecordingLLM([_reply({"do": [], "face": "sad", "say": "I am here."})])
    res = await run_turn(llm, _dispatch(order), CHOTU, list(MEMORY), None)
    assert order == [] and res["line"] == "I am here." and res["face"] == "sad"


async def test_his_turn_returns_to_memory_as_the_same_json():
    """Uniform history is the point -- no prose-versus-tool-call pattern to drift
    towards."""
    llm = RecordingLLM([_reply({"do": [{"tool": "act", "args": {"name": "sit"}}],
                                "face": "idle", "say": "Sitting."})])
    res = await run_turn(llm, _dispatch([]), CHOTU, list(MEMORY), None)
    first = res["new"][0]
    assert first["role"] == "assistant"
    body = json.loads(first["content"])
    assert body["say"] == "Sitting." and body["face"] == "idle"
    assert body["do"] == [{"tool": "act", "args": {"name": "sit"}}]


async def test_memory_holds_only_his_own_turn():
    """The result is NOT written straight into memory -- it comes back as input
    and is recorded when its own turn runs, so it cannot be added twice."""
    llm = RecordingLLM([_reply({"do": [{"tool": "sense", "args": {"what": "battery"}}],
                                "face": "thinking", "say": "Checking."})])
    res = await run_turn(llm, _dispatch([], results={"sense": {"percent": 62}}),
                         CHOTU, list(MEMORY), None)
    assert len(res["new"]) == 1 and res["new"][0]["role"] == "assistant"


async def test_generation_is_capped_and_temperature_explicit():
    llm = RecordingLLM([_reply({"do": [], "face": "idle", "say": "Fine."})])
    await run_turn(llm, _dispatch([]), CHOTU, list(MEMORY), None)
    call = llm.calls[0]
    assert call["max_tokens"] == MAX_TOKENS
    assert call["temperature"] is not None, "the server default is 1.0, randomly seeded"
    assert call["tools"] is None, "this path does not use tool calling"


async def test_the_schema_constraint_is_sent_when_switched_on():
    llm = RecordingLLM([_reply({"do": [], "face": "idle", "say": "Fine."})])
    await run_turn(llm, _dispatch([]), CHOTU, list(MEMORY), None)
    rf = llm.calls[0].get("response_format")
    if CONSTRAIN:
        assert rf["json_schema"]["schema"] == TURN_SCHEMA
    else:
        assert rf is None


async def test_one_call_per_turn():
    llm = RecordingLLM([_reply({"do": [{"tool": "move", "args": {"direction": "forward"}}],
                                "face": "idle", "say": "Going."})])
    await run_turn(llm, _dispatch([]), CHOTU, list(MEMORY), None)
    assert len(llm.calls) == 1


async def test_internal_fields_never_reach_the_wire():
    llm = RecordingLLM([_reply({"do": [], "face": "idle", "say": "Fine."})])
    await run_turn(llm, _dispatch([]), CHOTU, list(MEMORY), None)
    assert not any(k.startswith("_") for m in llm.calls[0]["messages"] for k in m)


def test_the_persona_leads_the_prompt():
    msgs = turn_messages(CHOTU, MEMORY)
    assert msgs[0]["role"] == "system" and CHOTU in msgs[0]["content"]
    assert msgs[1]["content"] == "[human] go forward"


def test_strip_internal_fields_does_not_mutate_the_caller():
    src = [{"role": "user", "content": "x", "_origin": "user"}]
    strip_internal_fields(src)
    assert src[0]["_origin"] == "user"


def test_result_line_names_the_error_on_failure():
    line = result_line("move", {"direction": "forward"},
                       {"ok": False, "error": "movement stopped"})
    assert "movement stopped" in line and "move" in line


# --- the spoken line -------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ('"Bold choice."', "Bold choice."),
    ("“Bold choice.”", "Bold choice."),
    ('He said "no". Fine.', 'He said "no". Fine.'),
    ("", ""),
])
def test_strip_wrapping_quotes(raw, want):
    assert strip_wrapping_quotes(raw) == want


@pytest.mark.parametrize("raw,want", [
    ("Low margin. Low margin. Low margin.", "Low margin."),
    ("Done. Never again.", "Done. Never again."),
    ("Quiet", "Quiet"),
])
def test_collapse_repeats(raw, want):
    assert collapse_repeats(raw) == want


def test_clean_line_unwraps_and_deduplicates():
    assert clean_line('"Low margin. Low margin."') == "Low margin."


# --- the prompt and the code must name the same things ---------------------

def test_chotu_md_names_every_action_the_dispatch_accepts():
    """CHOTU.md lists the actions in prose because that is the part Rushi tunes.
    This is what stops it drifting from ACT_NAMES."""
    md = SYSTEM_PROMPT.lower()
    missing = [n for n in ACT_NAMES if n not in md]
    assert not missing, f"CHOTU.md never mentions: {missing}"


def test_chotu_md_names_every_sense_kind():
    md = SYSTEM_PROMPT.lower()
    assert not [k for k in SENSE_KINDS if k not in md]


def test_chotu_md_only_uses_real_faces():
    """A face the OLED does not have would silently fall back to idle."""
    import re
    quoted = set(re.findall(r'"face":\s*"([^"]+)"', SYSTEM_PROMPT))
    assert quoted, "the examples should show faces"
    assert not (quoted - set(FACES))


def test_chotu_md_teaches_the_answer_shape():
    assert '"do"' in SYSTEM_PROMPT and '"say"' in SYSTEM_PROMPT
    assert '"face"' in SYSTEM_PROMPT


def test_chotu_md_shows_a_sequence_example():
    """Multi-step commands measured 0/5 without one and 5/5 with."""
    import re
    seqs = [m for m in re.findall(r'"do":\s*\[(.*?)\]\s*,\s*"face"', SYSTEM_PROMPT)
            if m.count('"tool"') > 1]
    assert seqs, "CHOTU.md must show at least one multi-action example"


# --- results come back as input --------------------------------------------

async def test_a_reading_comes_back_for_a_reply():
    """A `sense` is worthless unless he says the number. The result is returned
    for the brain to push as input, so it takes its own turn."""
    llm = RecordingLLM([_reply({"do": [{"tool": "sense", "args": {"what": "battery"}}],
                                "face": "thinking", "say": "Checking."})])
    res = await run_turn(llm, _dispatch([], results={"sense": {"percent": 62}}),
                         CHOTU, list(MEMORY), None)
    assert len(res["replies"]) == 1 and "62" in res["replies"][0]


async def test_a_successful_move_does_not_ask_for_a_reply():
    """motion_done already arrives on its own; a second nudge would double it."""
    llm = RecordingLLM([_reply({"do": [{"tool": "move", "args": {"direction": "forward"}}],
                                "face": "idle", "say": "Going."})])
    res = await run_turn(llm, _dispatch([]), CHOTU, list(MEMORY), None)
    assert res["replies"] == []


async def test_a_failure_always_comes_back():
    llm = RecordingLLM([_reply({"do": [{"tool": "move", "args": {"direction": "forward"}}],
                                "face": "idle", "say": "Going."})])
    res = await run_turn(llm, _dispatch([], failing=("move",)), CHOTU, list(MEMORY), None)
    assert len(res["replies"]) == 1 and "movement stopped" in res["replies"][0]


# --- the robot taught us these two -----------------------------------------

class _SlowMotionDispatch:
    """A motion tool the way the real one behaves: returns a `started` ack in
    milliseconds while the servos run for seconds, and REFUSES a second motion
    until the first finishes. A dispatch that returns instantly cannot catch
    what this catches."""

    def __init__(self, order):
        self.order, self.busy = order, False

    async def __call__(self, **kwargs):
        if self.busy:
            return {"ok": False, "tool": "move", "result": None, "duration_ms": 0,
                    "timestamp": 0.0, "error": "motion in progress: ~1.6s remaining"}
        self.busy = True
        self.order.append(kwargs.get("direction") or kwargs.get("name"))
        return {"ok": True, "tool": "move",
                "result": {"status": "started", "eta_ms": 1600},
                "duration_ms": 0, "timestamp": 0.0, "error": None}


async def test_a_sequence_waits_for_the_legs_between_steps():
    """2026-08-09, on the real robot: "two forward, two back, two forward" only
    ever walked forward. The second step hit the motion lock and the
    abandon-on-failure rule dropped the rest."""
    order = []
    mover = _SlowMotionDispatch(order)
    llm = RecordingLLM([_reply({"do": [
        {"tool": "move", "args": {"direction": "forward", "steps": 2}},
        {"tool": "move", "args": {"direction": "backward", "steps": 2}},
        {"tool": "move", "args": {"direction": "turn left", "steps": 1}}],
        "face": "idle", "say": "Forward, back, then left."})])

    waits = []

    async def wait_motion(eta_ms):
        waits.append(eta_ms)
        mover.busy = False          # the legs stop

    res = await run_turn(llm, {"move": mover}, CHOTU, list(MEMORY), None,
                         wait_motion=wait_motion)
    assert order == ["forward", "backward", "turn left"], "the whole sequence must run"
    assert waits == [1600, 1600], "wait after every step but the last"
    assert len(res["outcomes"]) == 3


async def test_without_a_waiter_the_lock_still_stops_the_sequence():
    """The wait is what fixes it -- proving the test can fail."""
    order = []
    llm = RecordingLLM([_reply({"do": [
        {"tool": "move", "args": {"direction": "forward"}},
        {"tool": "move", "args": {"direction": "backward"}}],
        "face": "idle", "say": "x"})])
    res = await run_turn(llm, {"move": _SlowMotionDispatch(order)}, CHOTU,
                         list(MEMORY), None, wait_motion=None)
    assert order == ["forward"] and len(res["outcomes"]) == 2


async def test_a_motion_that_never_finishes_abandons_the_rest_and_says_so():
    """The ETA is a guess and the real walk is slower. If the legs are still
    going when the wait gives up, dispatching would only earn a lock rejection
    -- stop, and report it, rather than half-running the sequence in silence."""
    order = []
    llm = RecordingLLM([_reply({"do": [
        {"tool": "move", "args": {"direction": "forward"}},
        {"tool": "move", "args": {"direction": "backward"}}],
        "face": "idle", "say": "x"})])

    async def never_finishes(eta_ms):
        return False

    res = await run_turn(llm, {"move": _SlowMotionDispatch(order)}, CHOTU,
                         list(MEMORY), None, wait_motion=never_finishes)
    assert order == ["forward"]
    assert len(res["outcomes"]) == 1, "the second step must not be dispatched"
    assert any("dropped" in r for r in res["replies"])
