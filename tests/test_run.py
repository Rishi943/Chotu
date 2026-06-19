from pathlib import Path

from core.launcher import LauncherState
from core.run import plan_services


def test_plan_local_preset_spawns_llama_and_checks_both():
    plan = plan_services(LauncherState(preset_idx=1), Path("/repo"), "http://pi:7000")
    assert plan.spawn_llama is True
    assert "/repo/models/Qwen3.5-4B-Q4_K_M.gguf" in plan.llama_args
    names = [n for n, _ in plan.health_checks]
    assert names == ["llama", "pi"]
    assert ("llama", "http://127.0.0.1:8080/health") in plan.health_checks


def test_plan_dashscope_no_llama_pi_only():
    plan = plan_services(LauncherState(preset_idx=2), Path("/repo"), "http://pi:7000")
    assert plan.spawn_llama is False and plan.llama_args == []
    assert [n for n, _ in plan.health_checks] == ["pi"]


def test_plan_offline_skips_pi_check():
    plan = plan_services(LauncherState(preset_idx=3, pi_mode="offline"), Path("/repo"), "http://pi:7000")
    assert plan.health_checks == []


def test_plan_pi_start_sets_start_bridge():
    plan = plan_services(LauncherState(preset_idx=1, pi_mode="start"), Path("/repo"), "http://pi:7000")
    assert plan.start_bridge is True
