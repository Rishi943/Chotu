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
