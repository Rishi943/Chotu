"""Orchestrator for `./launch.sh`: config screen -> services -> brain (foreground).

Pure planning (`plan_services`) is separated from side-effecting helpers so the
decision logic is unit-tested. `main()` wires them and hands off to core.brain."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

from core.launcher import PRESETS, LauncherState, llama_args, run_launcher

LLAMA_HEALTH = "http://127.0.0.1:8080/health"
REPO = Path(__file__).resolve().parent.parent


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


def _http_ok(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2.0).status_code == 200
    except Exception:
        return False


def wait_healthy(checks, probe=_http_ok, timeout=120.0, interval=1.0, sleep=time.sleep) -> None:
    """Poll each (name, url) until all return True, or raise TimeoutError after `timeout`."""
    deadline = time.monotonic() + timeout
    pending = list(checks)
    while pending:
        pending = [(n, u) for (n, u) in pending if not probe(u)]
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"unhealthy after {timeout}s: {[n for n, _ in pending]}")
        sleep(interval)


def spawn_llama(args: list[str], log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "w")
    return subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def spawn_bridge(log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "w")
    cmd = ["ssh", "chotu@chotu.local",
           "sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py"]
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def teardown(procs) -> None:
    for p in procs:
        try:
            p.terminate()
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
        except Exception:
            pass


def main() -> None:
    load_dotenv()                                   # PI_HOST + cloud creds for orchestration
    pi_host = os.getenv("PI_HOST", "http://chotu.local:7000")
    state = run_launcher()                          # config screen → os.environ set
    plan = plan_services(state, REPO, pi_host)

    procs: list[subprocess.Popen] = []
    if plan.spawn_llama:
        print("  starting llama-server …  (logs: out/llama.log)")
        procs.append(spawn_llama(plan.llama_args, REPO / "out" / "llama.log"))
    if plan.start_bridge:
        print("  starting Pi bridge over SSH …  (logs: out/bridge.log)")
        spawn_bridge(REPO / "out" / "bridge.log")   # fire-and-forget; left running on exit
    if not plan.spawn_llama:                        # DashScope + Claude are both cloud
        print("  cloud preset selected — this spends tokens.")

    if plan.health_checks:
        print(f"  waiting for: {', '.join(n for n, _ in plan.health_checks)} …")
        try:
            wait_healthy(plan.health_checks)
        except TimeoutError as e:
            print(f"  ✗ {e}")
            teardown(procs)
            sys.exit(1)

    os.environ["PALIV_NO_LAUNCHER"] = "1"
    import core.brain                                # late import: brain reads env at import time
    try:
        asyncio.run(core.brain.main())
    finally:
        teardown(procs)                              # kills llama; bridge left running


if __name__ == "__main__":
    main()
