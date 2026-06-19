"""Unit tests for the pre-launch config screen (pure-logic parts only)."""

import os

from core.launcher import LauncherState, PRESETS
import core.launcher as launcher


def test_presets_order_and_content():
    labels = [p["label"] for p in PRESETS]
    assert labels == ["Gemma", "Qwen", "Qwen cloud", "Claude"]
    gemma, qwen, qcloud, claude = PRESETS
    assert gemma["provider"] == "local" and gemma["spawn_llama"] is True
    assert gemma["model"] == "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    assert qwen["spawn_llama"] is True and qwen["model"] == "Qwen3.5-4B-Q4_K_M.gguf"
    assert qcloud["provider"] == "local" and qcloud["spawn_llama"] is False
    assert qcloud["model"] == "qwen3.5-flash"
    assert claude["provider"] == "claude" and claude["spawn_llama"] is False


def test_llama_args_gemma_has_swa_full():
    from pathlib import Path
    args = launcher.llama_args(PRESETS[0], Path("/m"))
    assert args[0] == "llama-server"
    assert "/m/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf" in args
    assert "/m/gemma_mmproj-BF16.gguf" in args
    assert "--swa-full" in args


def test_llama_args_qwen_no_swa_full():
    from pathlib import Path
    args = launcher.llama_args(PRESETS[1], Path("/m"))
    assert "/m/mmproj-BF16.gguf" in args
    assert "--swa-full" not in args


def test_to_env_defaults_qwen_local_overrides_url():
    s = LauncherState()  # preset_idx 1 = Qwen (local llama)
    env = s.to_env()
    assert env["PALIV_LLM_PROVIDER"] == "local"
    assert env["PALIV_BRAIN_MODEL"] == "Qwen3.5-4B-Q4_K_M.gguf"
    assert env["PALIV_BRAIN_URL"] == "http://127.0.0.1:8080/v1"
    assert env["PALIV_BRAIN_KEY"] == ""
    assert env["PALIV_VOICE"] == "0" and env["PALIV_PTT"] == "0"
    assert env["PALIV_MUTE"] == "0" and env["PALIV_DEBUG"] == "0"
    assert env["PALIV_SHOW_STATS"] == "0" and env["PALIV_PERSONA"] == ""


def test_to_env_dashscope_leaves_url_unset():
    s = LauncherState(preset_idx=2)  # Qwen cloud
    env = s.to_env()
    assert env["PALIV_BRAIN_MODEL"] == "qwen3.5-flash"
    assert "PALIV_BRAIN_URL" not in env
    assert "PALIV_BRAIN_KEY" not in env


def test_to_env_claude_provider():
    s = LauncherState(preset_idx=3)
    env = s.to_env()
    assert env["PALIV_LLM_PROVIDER"] == "claude"
    assert "PALIV_BRAIN_URL" not in env


def test_to_env_input_modes_map_to_voice_ptt():
    assert LauncherState(input_mode="text").to_env()["PALIV_VOICE"] == "0"
    voice = LauncherState(input_mode="voice").to_env()
    assert voice["PALIV_VOICE"] == "1" and voice["PALIV_PTT"] == "0"
    ptt = LauncherState(input_mode="ptt").to_env()
    assert ptt["PALIV_PTT"] == "1" and ptt["PALIV_VOICE"] == "0"


def test_seed_empty_env_is_qwen_base_off():
    s = LauncherState.seed_from_env({})
    assert s.preset_idx == 1        # Qwen
    assert s.persona == "base"
    assert s.input_mode == "text"
    assert not (s.mute or s.debug or s.stats)


def test_seed_mute_flag_checks_mute():
    s = LauncherState.seed_from_env({"PALIV_MUTE": "1"})
    assert s.mute is True
    assert s.debug is False


def test_seed_claude_provider_selects_claude():
    s = LauncherState.seed_from_env({"PALIV_LLM_PROVIDER": "claude"})
    assert s.preset_idx == 3        # Claude


def test_seed_dashscope_model_selects_qwen_cloud():
    s = LauncherState.seed_from_env({"PALIV_BRAIN_MODEL": "qwen3.5-flash"})
    assert s.preset_idx == 2


def test_seed_gemma_model_selects_gemma():
    s = LauncherState.seed_from_env(
        {"PALIV_LLM_PROVIDER": "local", "PALIV_BRAIN_MODEL": "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"})
    assert s.preset_idx == 0        # Gemma


def test_seed_reel_persona():
    s = LauncherState.seed_from_env({"PALIV_PERSONA": "reel"})
    assert s.persona == "reel"


def test_seed_input_and_stats_and_pi_default():
    s = LauncherState.seed_from_env({"PALIV_PTT": "1", "PALIV_SHOW_STATS": "1"})
    assert s.input_mode == "ptt" and s.stats is True
    assert s.pi_mode == "running"
    assert LauncherState.seed_from_env({"PALIV_VOICE": "1"}).input_mode == "voice"


def test_down_moves_focus_and_wraps():
    s = LauncherState(focus=0)
    action, s = s.apply_key("DOWN")
    assert action == "continue" and s.focus == 1
    s = LauncherState(focus=10)
    _, s = s.apply_key("DOWN")
    assert s.focus == 0


def test_up_wraps_to_last():
    s = LauncherState(focus=0)
    _, s = s.apply_key("UP")
    assert s.focus == 10


def test_select_on_preset_row_is_radio():
    s = LauncherState(focus=0, preset_idx=1)   # focus on Gemma row
    _, s = s.apply_key("SELECT")
    assert s.preset_idx == 0                    # selecting Gemma replaces Qwen


def test_select_input_row_cycles():
    s = LauncherState(focus=5, input_mode="text")
    _, s = s.apply_key("SELECT"); assert s.input_mode == "voice"
    _, s = s.apply_key("SELECT"); assert s.input_mode == "ptt"
    _, s = s.apply_key("SELECT"); assert s.input_mode == "text"


def test_select_pi_bridge_row_cycles():
    s = LauncherState(focus=9, pi_mode="running")
    _, s = s.apply_key("SELECT"); assert s.pi_mode == "start"
    _, s = s.apply_key("SELECT"); assert s.pi_mode == "offline"
    _, s = s.apply_key("SELECT"); assert s.pi_mode == "running"


def test_select_stats_toggle():
    s = LauncherState(focus=8)
    _, s = s.apply_key("SELECT"); assert s.stats is True


def test_select_on_toggle_flips_only_that_toggle():
    s = LauncherState(focus=6)                  # mute row
    _, s = s.apply_key("SELECT")
    assert s.mute is True and s.debug is False
    _, s = s.apply_key("SELECT")
    assert s.mute is False


def test_select_on_persona_cycles():
    s = LauncherState(focus=4, persona="base")
    _, s = s.apply_key("SELECT")
    assert s.persona == "reel"
    _, s = s.apply_key("SELECT")
    assert s.persona == "base"


def test_select_on_start_returns_start():
    s = LauncherState(focus=10)
    action, _ = s.apply_key("SELECT")
    assert action == "start"


def test_quit_key_returns_quit():
    action, _ = LauncherState().apply_key("QUIT")
    assert action == "quit"


def test_unknown_key_is_noop():
    s = LauncherState(focus=2)
    action, s2 = s.apply_key("?")
    assert action == "continue" and s2.focus == 2


def test_render_shows_new_rows():
    text = LauncherState(preset_idx=0, mute=True, stats=True, persona="reel").render()
    assert "Gemma" in text and "Qwen cloud" in text and "Claude" in text
    assert "Input:" in text and "Pi bridge:" in text
    assert "[✓] Mute" in text and "[✓] Stats" in text


def test_run_launcher_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("PALIV_NO_LAUNCHER", "1")
    monkeypatch.setenv("PALIV_BRAIN_MODEL", "sentinel-unchanged")
    state = launcher.run_launcher()
    assert os.environ["PALIV_BRAIN_MODEL"] == "sentinel-unchanged"
    assert isinstance(state, LauncherState)


def test_run_launcher_noop_when_not_tty(monkeypatch):
    monkeypatch.delenv("PALIV_NO_LAUNCHER", raising=False)
    monkeypatch.setattr(launcher.sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("PALIV_BRAIN_MODEL", "sentinel-unchanged")
    state = launcher.run_launcher()
    assert os.environ["PALIV_BRAIN_MODEL"] == "sentinel-unchanged"
    assert isinstance(state, LauncherState)
