"""Tests for PALIV_PERSONA persona selection in core.prompts."""

from core.prompts import load_system_prompt


def test_default_persona_is_base(monkeypatch):
    monkeypatch.delenv("PALIV_PERSONA", raising=False)
    prompt = load_system_prompt()
    # CHOTU_BASE.md's opening persona line
    assert "low tolerance for wasted potential" in prompt


def test_reel_persona_selected(monkeypatch):
    monkeypatch.setenv("PALIV_PERSONA", "reel")
    prompt = load_system_prompt()
    # CHOTU_REEL.md's frame line (first-boot premise)
    assert "first time inhabiting a physical body" in prompt
    # and it must NOT contain the everyday-persona opener
    assert "low tolerance for wasted potential" not in prompt


def test_unknown_persona_falls_back_to_base(monkeypatch):
    monkeypatch.setenv("PALIV_PERSONA", "banana")
    prompt = load_system_prompt()
    assert "low tolerance for wasted potential" in prompt
