"""Benchmark local GGUF models running on llama-server.

For each .gguf in models/ (or a specified model), auto-starts llama-server,
runs the Chotu workload (fake Pi + rotating JPEG frames + full system prompt +
tools), collects per-call llama.cpp timings, and writes a markdown comparison
table to 'out/'.

Usage:
    python -m scripts.benchmark_models                   # all models, 8 iters each
    python -m scripts.benchmark_models --iters 12
    python -m scripts.benchmark_models --model Qwen3.5-4B-Q4_K_M.gguf
    python -m scripts.benchmark_models --llama-server /path/to/llama-server
    python -m scripts.benchmark_models --warmup 2       # discard first 2 calls (default: 1)

NOTE: this script kills any process on port 8080 to take over llama-server.
"""

import argparse
import asyncio
import base64
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

# Silence TTS / voice / PTT before brain is ever imported
os.environ.setdefault("PALIV_MUTE", "1")
os.environ.setdefault("PALIV_VOICE", "0")
os.environ.setdefault("PALIV_PTT", "0")

REPO = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO / "models"
OUTPUT_DIR = REPO / "out"
PORT = 8080
HEALTH_URL = f"http://localhost:{PORT}/health"
STARTUP_TIMEOUT = 120  # seconds to wait for llama-server to become healthy

# ── Frame assets (same source as sim_loop) ────────────────────────────────────

_FRAME_PATHS = sorted((REPO / "assets").glob("*.jpeg"))[:3]
_FRAMES: list[str] = [base64.b64encode(p.read_bytes()).decode() for p in _FRAME_PATHS]
if not _FRAMES:
    print("WARNING: no .jpeg files in assets/ — vision frames disabled", flush=True)


# ── llama-server lifecycle ────────────────────────────────────────────────────

def _find_llama_server(override: Optional[str]) -> str:
    if override:
        return override
    found = shutil.which("llama-server")
    if found:
        return found
    raise SystemExit("llama-server not found in PATH. Install it or pass --llama-server.")


def _kill_port(port: int) -> None:
    """Best-effort: kill any process holding the port."""
    try:
        r = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
        for pid in r.stdout.strip().split():
            if pid:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if r.stdout.strip():
            time.sleep(1.5)
    except Exception:
        pass


def _start_server(llama_bin: str, model: Path, mmproj: Optional[Path],
                  ctx_size: int = 16384) -> subprocess.Popen:
    cmd = [llama_bin, "-m", str(model), "--port", str(PORT),
           "-ngl", "99", "-c", str(ctx_size), "--parallel", "1"]
    if mmproj:
        cmd += ["--mmproj", str(mmproj)]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def _wait_healthy(timeout: int = STARTUP_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(HEALTH_URL, timeout=3.0)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(2)
    return False


# ── Fake workload fixtures ────────────────────────────────────────────────────

class _FakePi:
    def __init__(self):
        self._i = 0

    async def capture(self) -> dict:
        if not _FRAMES:
            return {"ok": False, "tool": "capture", "result": {}}
        b64 = _FRAMES[self._i % len(_FRAMES)]
        self._i += 1
        return {"ok": True, "tool": "capture", "result": {"image_base64": b64}}


def _fake_dispatch() -> dict:
    import time as _t

    def _env(tool: str, **kw) -> dict:
        r: dict = {"ok": True, "tool": tool, "duration_ms": 5,
                   "timestamp": _t.time(), "error": None, "result": {}}
        if tool == "get_distance":
            r["result"] = {"cm": -1.0, "reliable": False}
        elif tool == "move":
            r["result"] = {"direction": kw.get("direction", "forward"),
                           "steps_completed": kw.get("steps", 1), "halted_early": False}
        elif tool == "pose":
            r["result"] = {"pose": kw.get("name", "stand"), "held_ms": 500}
        elif tool == "speak":
            r["result"] = {"text": kw.get("text", ""), "played": True}
        return r

    names = ["move", "pose", "speak", "get_distance", "get_battery", "set_face", "wait"]

    def _make(n):
        async def fn(**kw): return _env(n, **kw)
        return fn

    return {n: _make(n) for n in names}


# ── Per-call data ─────────────────────────────────────────────────────────────

def _query_vram_mb() -> Optional[int]:
    """Return current GPU memory used in MiB, or None if nvidia-smi unavailable."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return int(r.stdout.strip().split("\n")[0])  # first GPU
    except Exception:
        pass
    return None


class _Call:
    __slots__ = ("gen_tps", "prompt_tps", "ttft_ms", "gen_tokens", "tool_call", "latency_ms")

    def __init__(self, timings: dict, tool_call: bool, gen_tokens: int, latency_ms: float):
        self.gen_tps = timings.get("predicted_per_second", 0.0)
        self.prompt_tps = timings.get("prompt_per_second", 0.0)
        self.ttft_ms = timings.get("prompt_ms", 0.0)
        self.gen_tokens = gen_tokens
        self.tool_call = tool_call
        self.latency_ms = latency_ms


# ── Workload runner ───────────────────────────────────────────────────────────

async def _run_workload(model_name: str, n_iters: int) -> list[_Call]:
    """Reset brain globals, connect to (re)started server, run N iterations."""
    import core.brain as brain
    from core.llm_client import LLMClient
    from core.scratchpad import Scratchpad

    # Reconnect to the freshly started server with the correct model name
    await brain.llm_client.close()
    os.environ["PALIV_LLM_PROVIDER"] = "local"
    os.environ["PALIV_BRAIN_URL"] = f"http://localhost:{PORT}/v1"
    os.environ["PALIV_BRAIN_KEY"] = "not-needed"
    os.environ["PALIV_BRAIN_MODEL"] = model_name
    brain.llm_client = LLMClient()

    # Reset all stateful brain globals (matches sim_loop pattern)
    brain.pi = _FakePi()
    brain.dispatch_map = _fake_dispatch()
    brain._pi_reachable = False
    brain.memory.clear()
    brain.frame_stack.clear()
    brain.scratchpad = Scratchpad()
    brain._usage = {"calls": 0, "prompt": 0, "completion": 0, "cached": 0, "t0": None}
    brain.pending_input.drain()
    brain.pending_input.push("walk around and look for something interesting")

    # Tap into chat_complete to capture per-call llama.cpp timings
    calls: list[_Call] = []
    _orig = brain.llm_client.chat_complete

    async def _tap(messages, tools, **kw):
        t0 = time.monotonic()
        resp = await _orig(messages, tools, **kw)
        latency_ms = (time.monotonic() - t0) * 1000
        u = resp.usage or {}
        t = u.get("timings", {})
        had_tool_call = bool(resp.choices and resp.choices[0].message.tool_calls)
        if t:
            calls.append(_Call(
                timings=t,
                tool_call=had_tool_call,
                gen_tokens=u.get("completion_tokens", 0),
                latency_ms=latency_ms,
            ))
        return resp

    brain.llm_client.chat_complete = _tap

    for _ in range(n_iters):
        try:
            await brain.run_iteration()
        except Exception as e:
            print(f"     [warn] {e}", flush=True)

    await brain.llm_client.close()
    return calls


# ── Markdown table ────────────────────────────────────────────────────────────

def _fmt(vals: list[float]) -> str:
    if not vals:
        return "—"
    m = mean(vals)
    s = stdev(vals) if len(vals) > 1 else 0.0
    return f"{m:.1f} ±{s:.1f}"


def _summary_table(rows: list[dict]) -> str:
    has_vram = any(r.get("vram_mb") is not None for r in rows if "data" in r)
    vram_hdr = " | VRAM (MiB)" if has_vram else ""
    vram_sep = "|---" if has_vram else ""
    hdr = (
        f"| Model | Calls | Gen (tok/s) | Prompt eval (tok/s)"
        f" | Call latency (ms) | Tool-call rate | Avg gen tok | Total time{vram_hdr} |\n"
        f"|---|---|---|---|---|---|---|---{vram_sep}|\n"
    )
    lines = [hdr]
    for r in rows:
        name = f"`{r['model']}`"
        if "error" in r:
            n_dash = 6 + (1 if has_vram else 0)
            lines.append(f"| {name}{' | —' * n_dash} | ❌ {r['error']} |\n")
            continue
        d: list[_Call] = r["data"]
        elapsed = r.get("elapsed_s", 0)
        vram = r.get("vram_mb")
        vram_cell = f" | {vram}" if has_vram else ""
        if not d:
            lines.append(f"| {name} | 0 | — | — | — | — | — | {elapsed:.0f}s{vram_cell} |\n")
            continue
        tool_rate = f"{sum(c.tool_call for c in d) / len(d):.0%}"
        avg_gen = f"{mean(c.gen_tokens for c in d):.0f}"
        lines.append(
            f"| {name} | {len(d)}"
            f" | {_fmt([c.gen_tps for c in d])}"
            f" | {_fmt([c.prompt_tps for c in d])}"
            f" | {_fmt([c.latency_ms for c in d])}"
            f" | {tool_rate}"
            f" | {avg_gen}"
            f" | {elapsed:.0f}s"
            f"{vram_cell} |\n"
        )
    return "".join(lines)


def _latency_breakdown(rows: list[dict], warmup: int) -> str:
    """Per-call latency table for each model showing cold→warm cache progression."""
    lines = ["## Per-call latency progression\n\n"]
    lines.append("First column includes the warmup call(s) so you can see the cold-cache baseline.\n\n")
    for r in rows:
        if "error" in r or not r.get("all_calls"):
            continue
        all_calls: list[_Call] = r["all_calls"]
        lines.append(f"### `{r['model']}`\n\n")
        lines.append("| Call | Latency (ms) | Prompt eval (ms) | Gen (tok/s) | Gen tok | Tool call? |\n")
        lines.append("|---|---|---|---|---|---|\n")
        for i, c in enumerate(all_calls):
            tag = " *(warmup)*" if i < warmup else ""
            lines.append(
                f"| {i+1}{tag}"
                f" | {c.latency_ms:.0f}"
                f" | {c.ttft_ms:.0f}"
                f" | {c.gen_tps:.1f}"
                f" | {c.gen_tokens}"
                f" | {'✓' if c.tool_call else '—'} |\n"
            )
        lines.append("\n")
    return "".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iters", type=int, default=30,
                    help="Iterations per model (default: 30)")
    ap.add_argument("--warmup", type=int, default=1,
                    help="Discard first N calls from metrics to skip cold-cache skew (default: 1)")
    ap.add_argument("--model", metavar="FILENAME",
                    help="Single .gguf filename to test (default: all in models/)")
    ap.add_argument("--llama-server", dest="llama_bin", metavar="PATH",
                    help="Path to llama-server binary (default: auto-detect from PATH)")
    ap.add_argument("--ctx-size", type=int, default=16384,
                    help="Context window size passed to llama-server (default: 16384)")
    args = ap.parse_args()

    llama_bin = _find_llama_server(args.llama_bin)
    mmproj = next(MODELS_DIR.glob("mmproj*.gguf"), None)

    if args.model:
        model_files = [MODELS_DIR / args.model]
        if not model_files[0].exists():
            raise SystemExit(f"Model not found: {model_files[0]}")
    else:
        model_files = [p for p in sorted(MODELS_DIR.glob("*.gguf"))
                       if "mmproj" not in p.name.lower()]

    if not model_files:
        raise SystemExit(f"No model .gguf files found in {MODELS_DIR}")

    print(f"\n{'─'*64}")
    print(f"  Models    : {len(model_files)}")
    print(f"  Iters each: {args.iters}  (warmup discards first {args.warmup})")
    print(f"  ctx-size  : {args.ctx_size}")
    print(f"  llama-bin : {llama_bin}")
    print(f"  mmproj    : {mmproj.name if mmproj else 'none'}")
    print(f"  frames    : {[p.name for p in _FRAME_PATHS] or ['none (no assets)']}")
    print(f"  ⚠ will kill any process on port {PORT}")
    print(f"{'─'*64}\n")

    rows: list[dict] = []
    proc: Optional[subprocess.Popen] = None

    try:
        for model_path in model_files:
            name = model_path.name
            print(f"── {name}")

            # Shut down previous instance
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                proc = None
            _kill_port(PORT)
            await asyncio.sleep(1)

            print(f"   starting llama-server...", flush=True)
            proc = _start_server(llama_bin, model_path, mmproj, args.ctx_size)

            if not await _wait_healthy():
                print(f"   ✗ never became healthy after {STARTUP_TIMEOUT}s")
                rows.append({"model": name, "error": f"startup timeout ({STARTUP_TIMEOUT}s)"})
                continue

            print(f"   ✓ server ready — running {args.iters} iterations...", flush=True)
            t0 = time.monotonic()
            try:
                all_calls = await _run_workload(name, args.iters)
                elapsed = time.monotonic() - t0
                # Measured calls exclude warmup; all_calls kept for latency breakdown
                measured = all_calls[args.warmup:] if len(all_calls) > args.warmup else all_calls
                vram_mb = _query_vram_mb()
                gen_avg = mean([c.gen_tps for c in measured]) if measured else 0.0
                print(f"   ✓ {elapsed:.0f}s total — {len(measured)} measured calls"
                      f" ({args.warmup} warmup discarded)"
                      f" — {gen_avg:.1f} tok/s avg gen"
                      f"{f'  — VRAM {vram_mb} MiB' if vram_mb else ''}\n")
                rows.append({"model": name, "data": measured, "all_calls": all_calls,
                             "elapsed_s": elapsed, "vram_mb": vram_mb})
            except Exception as e:
                print(f"   ✗ workload error: {e}\n")
                rows.append({"model": name, "error": str(e)})

    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        _kill_port(PORT)

    # Build and save the markdown table
    OUTPUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out = OUTPUT_DIR / f"model_benchmark_{ts}.md"

    summary = _summary_table(rows)
    breakdown = _latency_breakdown(rows, args.warmup)
    vision_note = (f"vision ({len(_FRAME_PATHS)} rotating JPEGs)" if _FRAMES else "no vision")
    md = "\n".join([
        f"# Model Benchmark — {ts}",
        "",
        f"Workload: **{args.iters} iters/model** (first {args.warmup} discarded as warmup)"
        f" · ctx {args.ctx_size} · fake Pi · {vision_note}"
        f" · full Chotu system prompt + tool schemas",
        "",
        "## Summary",
        "",
        summary,
        "**Metric notes:**",
        "- **Gen (tok/s)** — llama.cpp `predicted_per_second`: token generation throughput",
        "- **Prompt eval (tok/s)** — llama.cpp `prompt_per_second`: context ingestion speed",
        "- **Call latency (ms)** — wall-clock time from input sent to response received"
        " (includes prompt eval + generation; cold-cache warmup discarded from average)",
        "- **Tool-call rate** — fraction of LLM turns that produced ≥1 tool call (vs. free text)",
        "- **Avg gen tok** — mean completion tokens per turn",
        "- **Total time** — wall time for the full model run including server startup",
        "- **VRAM (MiB)** — GPU memory used after the run (nvidia-smi, first GPU)",
        "- ±N = standard deviation across measured calls",
        "",
        breakdown,
    ])
    out.write_text(md)
    print(f"\nSaved → {out}\n")
    print(summary)


if __name__ == "__main__":
    asyncio.run(main())
