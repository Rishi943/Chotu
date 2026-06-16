"""Unit tests for the pre-launch config screen (pure-logic parts only)."""

import os

from core.launcher import LauncherState, PRESETS
import core.launcher as launcher


def test_presets_order_and_content():
    labels = [p["label"] for p in PRESETS]
    assert labels == ["Gemma", "Qwen", "Claude"]
    gemma, qwen, claude = PRESETS
    assert gemma["provider"] == "local"
    assert gemma["model"] == "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    assert qwen["provider"] == "local"
    assert qwen["model"] == "Qwen3.5-4B-Q4_K_M.gguf"
    assert claude["provider"] == "claude"
    assert claude["model"] == "claude-sonnet-4-6"


def test_to_env_defaults_qwen_base_all_off():
    s = LauncherState()  # preset_idx defaults to 1 (Qwen), all toggles off, base persona
    env = s.to_env()
    assert env["PALIV_LLM_PROVIDER"] == "local"
    assert env["PALIV_BRAIN_MODEL"] == "Qwen3.5-4B-Q4_K_M.gguf"
    assert env["PALIV_MUTE"] == "0"
    assert env["PALIV_DEBUG"] == "0"
    assert env["PALIV_VOICE"] == "0"
    assert env["PALIV_PTT"] == "0"
    assert env["PALIV_PERSONA"] == ""


def test_seed_empty_env_is_qwen_base_off():
    s = LauncherState.seed_from_env({})
    assert s.preset_idx == 1        # Qwen
    assert s.persona == "base"
    assert not (s.mute or s.debug or s.voice or s.ptt)


def test_seed_mute_flag_checks_mute():
    s = LauncherState.seed_from_env({"PALIV_MUTE": "1"})
    assert s.mute is True
    assert s.debug is False


def test_seed_claude_provider_selects_claude():
    s = LauncherState.seed_from_env({"PALIV_LLM_PROVIDER": "claude"})
    assert s.preset_idx == 2        # Claude


def test_seed_gemma_model_selects_gemma():
    s = LauncherState.seed_from_env(
        {"PALIV_LLM_PROVIDER": "local", "PALIV_BRAIN_MODEL": "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"})
    assert s.preset_idx == 0        # Gemma


def test_seed_reel_persona():
    s = LauncherState.seed_from_env({"PALIV_PERSONA": "reel"})
    assert s.persona == "reel"


def test_down_moves_focus_and_wraps():
    s = LauncherState(focus=0)
    action, s = s.apply_key("DOWN")
    assert action == "continue" and s.focus == 1
    s = LauncherState(focus=8)
    _, s = s.apply_key("DOWN")
    assert s.focus == 0


def test_up_wraps_to_last():
    s = LauncherState(focus=0)
    _, s = s.apply_key("UP")
    assert s.focus == 8


def test_select_on_preset_row_is_radio():
    s = LauncherState(focus=0, preset_idx=1)   # focus on Gemma row
    _, s = s.apply_key("SELECT")
    assert s.preset_idx == 0                    # selecting Gemma replaces Qwen


def test_select_on_toggle_flips_only_that_toggle():
    s = LauncherState(focus=3)                  # mute row
    _, s = s.apply_key("SELECT")
    assert s.mute is True and s.debug is False
    _, s = s.apply_key("SELECT")
    assert s.mute is False


def test_select_on_persona_cycles():
    s = LauncherState(focus=7, persona="base")
    _, s = s.apply_key("SELECT")
    assert s.persona == "reel"
    _, s = s.apply_key("SELECT")
    assert s.persona == "base"


def test_select_on_start_returns_start():
    s = LauncherState(focus=8)
    action, _ = s.apply_key("SELECT")
    assert action == "start"


def test_quit_key_returns_quit():
    action, _ = LauncherState().apply_key("QUIT")
    assert action == "quit"


def test_unknown_key_is_noop():
    s = LauncherState(focus=2)
    action, s2 = s.apply_key("?")
    assert action == "continue" and s2.focus == 2


def test_render_shows_selected_preset_and_toggles():
    s = LauncherState(preset_idx=0, mute=True, persona="reel", focus=0)
    text = s.render()
    assert "Gemma" in text and "Qwen" in text and "Claude" in text
    assert "(•) Gemma" in text          # Gemma selected
    assert "[✓] Mute" in text           # mute on
    assert "reel" in text               # persona shown


def test_run_launcher_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("PALIV_NO_LAUNCHER", "1")
    monkeypatch.setenv("PALIV_BRAIN_MODEL", "sentinel-unchanged")
    launcher.run_launcher()
    assert os.environ["PALIV_BRAIN_MODEL"] == "sentinel-unchanged"


def test_run_launcher_noop_when_not_tty(monkeypatch):
    monkeypatch.delenv("PALIV_NO_LAUNCHER", raising=False)
    monkeypatch.setattr(launcher.sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("PALIV_BRAIN_MODEL", "sentinel-unchanged")
    launcher.run_launcher()
    assert os.environ["PALIV_BRAIN_MODEL"] == "sentinel-unchanged"
