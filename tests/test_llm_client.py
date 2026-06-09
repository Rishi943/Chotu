import os
from core.llm_client import LLMClient


def _client(provider, url=""):
    os.environ["PALIV_LLM_PROVIDER"] = provider
    if url:
        os.environ["PALIV_BRAIN_URL"] = url
    elif "PALIV_BRAIN_URL" in os.environ:
        del os.environ["PALIV_BRAIN_URL"]
    return LLMClient()


def test_supports_cache_control_local_dashscope_true():
    c = _client("local", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    assert c.supports_cache_control is True


def test_supports_cache_control_local_llama_false():
    c = _client("local", "http://localhost:8080/v1")
    assert c.supports_cache_control is False


def _ephemeral(block):
    return block.get("cache_control") == {"type": "ephemeral"}


def test_mark_cache_breakpoints_marks_system_and_boundary():
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "tool_call_id": "1", "content": "{}", "_cache_boundary": True},
        {"role": "user", "content": "frame"},  # volatile tail, must stay untouched
    ]
    out = LLMClient._mark_cache_breakpoints(msgs)
    # system marked
    assert _ephemeral(out[0]["content"][-1])
    # boundary (tool) marked, tag popped
    assert _ephemeral(out[2]["content"][-1])
    assert "_cache_boundary" not in out[2]
    # tail untouched (still a plain string, no marker)
    assert out[3]["content"] == "frame"


def test_mark_cache_breakpoints_system_only_when_no_boundary():
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
    out = LLMClient._mark_cache_breakpoints(msgs)
    assert _ephemeral(out[0]["content"][-1])
    assert out[1]["content"] == "hi"


def test_consolidate_tool_results_marks_boundary_block():
    msgs = [
        {"role": "tool", "tool_call_id": "1", "content": "{}", "_cache_boundary": True},
    ]
    out = LLMClient._consolidate_tool_results(msgs)
    assert out[0]["role"] == "user"
    assert out[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "_cache_boundary" not in out[0]


def test_consolidate_tool_results_unmarked_when_no_boundary():
    msgs = [{"role": "tool", "tool_call_id": "1", "content": "{}"}]
    out = LLMClient._consolidate_tool_results(msgs)
    assert "cache_control" not in out[0]["content"][-1]


def test_consolidate_tool_results_marks_non_tool_boundary_without_mutating_input():
    msg = {"role": "assistant", "content": [{"type": "text", "text": "a"}],
           "_cache_boundary": True}
    msgs = [msg]
    out = LLMClient._consolidate_tool_results(msgs)
    # boundary block marked, tag dropped on the output
    assert out[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "_cache_boundary" not in out[0]
    # caller's original dict is NOT mutated (no aliasing)
    assert msg["_cache_boundary"] is True
    assert "cache_control" not in msg["content"][-1]
