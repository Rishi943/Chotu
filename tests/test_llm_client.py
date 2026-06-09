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
