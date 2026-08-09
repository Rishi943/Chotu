def test_build_loop_messages_order():
    from core.brain import build_loop_messages
    from core.scratchpad import Scratchpad
    memory = [
        {"role": "user", "content": "[boot] hi", "_origin": "boot"},
        {"role": "assistant", "content": "ok"},
    ]
    frame_stack = [{"image_b64": "c", "motion": ""}]
    msgs = build_loop_messages("SYS", memory, frame_stack, Scratchpad())

    assert msgs[0] == {"role": "system", "content": "SYS"}
    # internal _origin fields stripped before sending
    assert all("_origin" not in m for m in msgs)
    # frames sit at the tail, after memory (empty scratchpad renders nothing)
    assert msgs[-1]["content"][1]["text"] == "[frame 0 | NOW — current view]"
    # memory content preserved in the middle
    assert {"role": "user", "content": "[boot] hi"} in msgs


def test_build_loop_messages_state_block_sits_before_frames():
    from core.brain import build_loop_messages
    from core.scratchpad import Scratchpad
    sp = Scratchpad()
    sp.update([("move", {"direction": "forward", "steps": 1}, {})])
    frame_stack = [{"image_b64": "c", "motion": ""}]
    msgs = build_loop_messages("SYS", [], frame_stack, sp)

    # order: system, [STATE], frame
    assert msgs[1]["content"].startswith("[STATE]")
    assert "_origin" not in msgs[1]
    assert msgs[-1]["content"][1]["text"] == "[frame 0 | NOW — current view]"


import pytest
from core.llm_client import (
    LLMResponse, NormalizedChoice, NormalizedMessage, ToolCall, ToolFunction,
)


class _FakeLLM:
    """Returns one scripted response, then text-only forever."""
    provider = "local"
    supports_cache_control = False
    def __init__(self, response):
        self._response = response
    async def chat_complete(self, messages, tools, **kw):
        return self._response
    def format_assistant_message(self, response):
        m = response.choices[0].message
        d = {"role": "assistant"}
        if m.content is not None:
            d["content"] = m.content
        return d
    def format_tool_result(self, tool_call_id, content):
        return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


class _FakePi:
    async def capture(self):
        return {"ok": True, "result": {"image_base64": "ZZZ"}}


@pytest.mark.asyncio
async def test_run_iteration_text_only_pushes_frame(monkeypatch):
    import core.brain as brain

    text_resp = LLMResponse(choices=[NormalizedChoice(
        message=NormalizedMessage(content="just standing here", tool_calls=None))])
    monkeypatch.setattr(brain, "llm_client", _FakeLLM(text_resp))
    monkeypatch.setattr(brain, "pi", _FakePi())
    monkeypatch.setattr(brain, "CAPTURE_EACH_TICK", True)
    brain.memory.clear()
    brain.frame_stack.clear()
    brain.pending_input.push("what are you doing")  # turn-based: no input, no turn

    dur = await brain.run_iteration()

    assert dur == 0.0  # no tools dispatched
    assert len(brain.frame_stack) == 1
    assert brain.frame_stack[0]["image_b64"] == "ZZZ"
    # His turn is stored as the JSON he produced. A reply that is not JSON at all
    # still yields the line rather than losing the turn.
    import json
    assistants = [m for m in brain.memory if m["role"] == "assistant"]
    assert json.loads(assistants[-1]["content"])["say"] == "just standing here"


@pytest.mark.asyncio
async def test_run_iteration_with_no_input_calls_no_model(monkeypatch):
    """Turn-based, 2026-08-09. The loop used to fire every LOOP_FLOOR seconds
    whether or not anyone had spoken, so one utterance produced a run of turns
    and each re-read memory and copied its own last line -- which is the whole
    source of the doubled and tripled speech in the 08-09 session log."""
    import core.brain as brain

    class _MustNotBeCalled:
        provider = "local"
        supports_cache_control = False

        async def chat_complete(self, *a, **kw):
            raise AssertionError("the model must not be called with no input")

    monkeypatch.setattr(brain, "llm_client", _MustNotBeCalled())
    monkeypatch.setattr(brain, "pi", _FakePi())
    brain.memory.clear()
    brain.frame_stack.clear()

    dur = await brain.run_iteration()

    assert dur == 0.0
    assert brain.memory == []


@pytest.mark.asyncio
async def test_run_iteration_event_input_no_human_prefix(monkeypatch):
    import core.brain as brain

    text_resp = LLMResponse(choices=[NormalizedChoice(
        message=NormalizedMessage(content="noted", tool_calls=None))])
    monkeypatch.setattr(brain, "llm_client", _FakeLLM(text_resp))
    monkeypatch.setattr(brain, "pi", _FakePi())
    brain.memory.clear()
    brain.frame_stack.clear()
    brain.pending_input.push("[event] motion_done: pose done")

    await brain.run_iteration()

    drained = [m for m in brain.memory if m["role"] == "user"]
    assert drained[0]["_origin"] == "event"
    assert not drained[0]["content"].startswith("[human] ")
    assert drained[0]["content"] == "[event] motion_done: pose done"


@pytest.mark.asyncio
async def test_run_iteration_user_input_gets_human_prefix(monkeypatch):
    import core.brain as brain

    text_resp = LLMResponse(choices=[NormalizedChoice(
        message=NormalizedMessage(content="hi there", tool_calls=None))])
    monkeypatch.setattr(brain, "llm_client", _FakeLLM(text_resp))
    monkeypatch.setattr(brain, "pi", _FakePi())
    brain.memory.clear()
    brain.frame_stack.clear()
    brain.pending_input.push("hello chotu")

    await brain.run_iteration()

    drained = [m for m in brain.memory if m["role"] == "user"]
    assert drained[0]["_origin"] == "user"
    assert drained[0]["content"] == "[human] hello chotu"


def test_build_loop_messages_tags_last_memory_when_cache_boundary():
    from core.brain import build_loop_messages
    from core.scratchpad import Scratchpad
    memory = [
        {"role": "assistant", "content": "a", "_origin": "loop"},
        {"role": "tool", "tool_call_id": "1", "content": "{}"},
    ]
    msgs = build_loop_messages("SYS", memory, [], Scratchpad(), cache_boundary=True)
    # the tool result (last memory msg) carries the boundary tag; nothing else does
    tagged = [m for m in msgs if m.get("_cache_boundary")]
    assert len(tagged) == 1
    assert tagged[0]["role"] == "tool"


def test_build_loop_messages_no_tag_when_cache_boundary_false():
    from core.brain import build_loop_messages
    from core.scratchpad import Scratchpad
    memory = [{"role": "assistant", "content": "a"}]
    msgs = build_loop_messages("SYS", memory, [], Scratchpad())  # default False
    assert all("_cache_boundary" not in m for m in msgs)
class _JsonTurnLLM:
    """Stub model on the 2026-08-09 path: ONE reply per turn, one JSON object
    carrying the actions, the face and the spoken line."""
    provider = "local"
    supports_cache_control = False

    def __init__(self, obj):
        self._obj = obj
        self.calls = 0

    async def chat_complete(self, messages, tools, **kw):
        import json
        self.calls += 1
        return LLMResponse(choices=[NormalizedChoice(message=NormalizedMessage(
            content=json.dumps(self._obj), tool_calls=None))])


@pytest.mark.asyncio
async def test_run_iteration_acts_and_speaks_in_one_call(monkeypatch):
    """One run_iteration() dispatches the action AND produces the spoken line,
    from a single model call — no second round trip."""
    import json
    import core.brain as brain

    llm = _JsonTurnLLM({"do": [{"tool": "sense", "args": {"what": "battery"}}],
                        "face": "thinking", "say": "Checking."})
    monkeypatch.setattr(brain, "llm_client", llm)
    monkeypatch.setattr(brain, "pi", _FakePi())
    tool_called = []

    async def _fake_sense(**kw):
        tool_called.append(kw)
        return {"ok": True, "tool": "sense", "result": {"percent": 57, "voltage": 12.0},
                "duration_ms": 1000, "timestamp": 0, "error": None}

    monkeypatch.setattr(brain, "dispatch_map", {"sense": _fake_sense})
    brain.memory.clear()
    brain.frame_stack.clear()
    brain.pending_input.push("how is my battery")

    dur = await brain.run_iteration()

    assert tool_called, "the sense tool must have been dispatched"
    assert llm.calls == 1, "an action turn must not cost a second call"
    assistants = [m for m in brain.memory if m["role"] == "assistant"]
    assert json.loads(assistants[-1]["content"])["say"] == "Checking."
    # The reading is queued as INPUT, so it takes its own turn and he actually
    # says the number instead of stopping at "Checking."
    assert "57" in brain.pending_input.drain()
    assert dur == 1.0


@pytest.mark.asyncio
async def test_run_iteration_runs_a_sequence_in_order(monkeypatch):
    """A multi-step command arrives as one reply and runs here, in order."""
    import core.brain as brain

    llm = _JsonTurnLLM({"do": [
        {"tool": "move", "args": {"direction": "forward", "steps": 2}},
        {"tool": "move", "args": {"direction": "backward", "steps": 2}},
        {"tool": "move", "args": {"direction": "turn left", "steps": 1}}],
        "face": "idle", "say": "Forward, back, then left."})
    monkeypatch.setattr(brain, "llm_client", llm)
    monkeypatch.setattr(brain, "pi", _FakePi())
    order = []

    async def _fake_move(**kw):
        order.append(kw.get("direction"))
        return {"ok": True, "tool": "move", "result": {"started": True},
                "duration_ms": 0, "timestamp": 0, "error": None}

    monkeypatch.setattr(brain, "dispatch_map", {"move": _fake_move})
    brain.memory.clear()
    brain.frame_stack.clear()
    brain.pending_input.push("two steps forward, two back, then one left")

    await brain.run_iteration()

    assert order == ["forward", "backward", "turn left"]
    assert llm.calls == 1
