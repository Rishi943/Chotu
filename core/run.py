"""Orchestrator for `./launch.sh`: config screen -> services -> brain (foreground).

Pure planning (`plan_services`) is separated from side-effecting helpers so the
decision logic is unit-tested. `main()` wires them and hands off to core.brain."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.launcher import PRESETS, LauncherState, llama_args

LLAMA_HEALTH = "http://127.0.0.1:8080/health"


@dataclass
class ServicePlan:
    spawn_llama: bool
    llama_args: list[str]
    health_checks: list[tuple[str, str]]
    start_bridge: bool


def plan_services(state: LauncherState, repo_root: Path, pi_host: str) -> ServicePlan:
    preset = PRESETS[state.preset_idx]
    spawn = bool(preset["spawn_llama"])
    args = llama_args(preset, repo_root / "models") if spawn else []
    checks: list[tuple[str, str]] = []
    if spawn:
        checks.append(("llama", LLAMA_HEALTH))
    if state.pi_mode != "offline":
        checks.append(("pi", f"{pi_host.rstrip('/')}/health"))
    return ServicePlan(spawn_llama=spawn, llama_args=args,
                       health_checks=checks, start_bridge=state.pi_mode == "start")
