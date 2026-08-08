"""The fork points at E2B on :8099, not at Qwen on :8080."""
import os
import pytest
from core.llm_client import LLMClient


def test_default_base_url_is_8099_by_ip(monkeypatch):
    for k in ("PALIV_BRAIN_URL", "PALIV_BRAIN_MODEL", "PALIV_LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    c = LLMClient()
    assert "127.0.0.1:8099" in c._openai.base_url.host + ":" + str(c._openai.base_url.port)
    assert "localhost" not in str(c._openai.base_url)


def test_default_model_is_e2b(monkeypatch):
    for k in ("PALIV_BRAIN_URL", "PALIV_BRAIN_MODEL", "PALIV_LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    c = LLMClient()
    assert "E2B" in c.model
