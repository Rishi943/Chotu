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


import subprocess

import pytest

from core import run as runmod


def test_wait_healthy_returns_when_all_ok():
    seen = {"n": 0}

    def probe(url):
        seen["n"] += 1
        return seen["n"] >= 2          # first poll False, then True

    runmod.wait_healthy([("llama", "u")], probe=probe, timeout=5, interval=0, sleep=lambda s: None)
    assert seen["n"] >= 2


def test_wait_healthy_times_out():
    with pytest.raises(TimeoutError):
        runmod.wait_healthy([("pi", "u")], probe=lambda u: False,
                            timeout=0.0, interval=0, sleep=lambda s: None)


class _FakeProc:
    def __init__(self):
        self.terminated = self.killed = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


def test_teardown_terminates_gracefully():
    p = _FakeProc()
    runmod.teardown([p])
    assert p.terminated and not p.killed


def test_teardown_kills_when_wait_times_out():
    p = _FakeProc()
    p.wait = lambda timeout=None: (_ for _ in ()).throw(subprocess.TimeoutExpired("p", timeout))
    runmod.teardown([p])
    assert p.killed
