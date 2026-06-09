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
from core.llm_client import LLMResponse, NormalizedChoice, NormalizedMessage


class _FakeLLM:
    """Returns one scripted response, then text-only forever."""
    provider = "local"
    supports_cache_control = False
    def __init__(self, response):
        self._response = response
    async def chat_complete(self, messages, tools, thinking=False):
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
    brain.memory.clear()
    brain.frame_stack.clear()

    dur = await brain.run_iteration()

    assert dur == 0.0  # no tools dispatched
    assert len(brain.frame_stack) == 1
    assert brain.frame_stack[0]["image_b64"] == "ZZZ"
    assistants = [m for m in brain.memory if m["role"] == "assistant"]
    assert assistants[-1]["content"] == "just standing here"


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
