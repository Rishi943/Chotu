"""Keep calling the model until it stops asking for tools."""
import json
import pytest
from core.turn import run_turn


class Call:
    def __init__(self, cid, name, args):
        self.id = cid
        self.function = type("F", (), {"name": name,
                                       "arguments": json.dumps(args)})()


class ScriptedLLM:
    """Returns a canned response per call, and counts the calls."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def chat_complete(self, messages, tools, **kw):
        self.calls += 1
        content, tool_calls = self.script.pop(0)
        msg = type("M", (), {"content": content, "tool_calls": tool_calls})()
        choice = type("C", (), {"message": msg})()
        return type("R", (), {"choices": [choice]})()

    def format_assistant_message(self, r):
        m = r.choices[0].message
        return {"role": "assistant", "content": m.content,
                "tool_calls": m.tool_calls}

    def format_tool_result(self, cid, content):
        return {"role": "tool", "tool_call_id": cid, "content": content}


async def fake_dispatch(_d, name, args):
    if name == "sense":
        return {"ok": True, "tool": "sense", "result": {"percent": 62},
                "duration_ms": 3, "timestamp": 0.0, "error": None}
    return {"ok": True, "tool": name, "result": {}, "duration_ms": 0,
            "timestamp": 0.0, "error": None}


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr("core.turn.dispatch_tool", fake_dispatch)


async def test_a_tool_and_its_answer_land_in_one_turn():
    llm = ScriptedLLM([
        (None, [Call("1", "sense", {"what": "battery"})]),
        ("You are at sixty two percent.", None),
    ])
    out = await run_turn(llm, {}, [{"role": "user", "content": "battery?"}])
    assert llm.calls == 2, "the loop must call the model again with the result"
    assert out[-1]["content"] == "You are at sixty two percent."
    assert any(m.get("role") == "tool" for m in out)


async def test_no_tool_calls_means_exactly_one_model_call():
    llm = ScriptedLLM([("hello", None)])
    out = await run_turn(llm, {}, [{"role": "user", "content": "hi"}])
    assert llm.calls == 1
    assert len(out) == 1


async def test_every_tool_call_gets_a_result_message():
    llm = ScriptedLLM([
        (None, [Call("a", "sense", {"what": "battery"}),
                Call("b", "sense", {"what": "distance"})]),
        ("done", None),
    ])
    out = await run_turn(llm, {}, [{"role": "user", "content": "look"}])
    ids = {m["tool_call_id"] for m in out if m.get("role") == "tool"}
    assert ids == {"a", "b"}


async def test_max_rounds_stops_a_runaway():
    llm = ScriptedLLM([(None, [Call(str(i), "sense", {"what": "battery"})])
                       for i in range(20)])
    out = await run_turn(llm, {}, [{"role": "user", "content": "go"}],
                         max_rounds=3)
    assert llm.calls == 3
    assert out[-1]["role"] == "tool"


async def test_events_are_emitted_for_the_console():
    seen = []
    llm = ScriptedLLM([
        (None, [Call("1", "sense", {"what": "battery"})]),
        ("sixty two", None),
    ])
    await run_turn(llm, {}, [{"role": "user", "content": "battery?"}],
                   on_event=lambda k, p: seen.append(k))
    assert "tool_call" in seen and "tool_result" in seen and "assistant" in seen
