"""Session profiler — attaches to LLMClient, writes a benchmark-format MD on shutdown.

Attach once at brain startup; call save() in the finally block.
Zero overhead between calls (just a list.append per LLM turn).
"""

import time
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev
from typing import Optional


class _Call:
    __slots__ = ("gen_tps", "prompt_tps", "prompt_ms", "latency_ms", "gen_tokens", "tool_call")

    def __init__(self, timings: dict, latency_ms: float, tool_call: bool, gen_tokens: int):
        self.gen_tps = timings.get("predicted_per_second", 0.0)
        self.prompt_tps = timings.get("prompt_per_second", 0.0)
        self.prompt_ms = timings.get("prompt_ms", 0.0)
        self.latency_ms = latency_ms
        self.gen_tokens = gen_tokens
        self.tool_call = tool_call


def _fmt(vals: list[float]) -> str:
    if not vals:
        return "—"
    m = mean(vals)
    s = stdev(vals) if len(vals) > 1 else 0.0
    return f"{m:.1f} ±{s:.1f}"


class SessionProfiler:
    def __init__(self):
        self._calls: list[_Call] = []

    def attach(self, llm_client) -> None:
        """Wrap llm_client.chat_complete to record per-call timings."""
        _orig = llm_client.chat_complete
        calls = self._calls

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
                    latency_ms=latency_ms,
                    tool_call=had_tool_call,
                    gen_tokens=u.get("completion_tokens", 0),
                ))
            return resp

        llm_client.chat_complete = _tap

    def save(self, output_dir: Path, model_name: str, pi_reachable: bool) -> Optional[Path]:
        """Write session MD to output_dir. Returns path written, or None if no data."""
        if not self._calls:
            return None

        output_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        out = output_dir / f"live_session_{ts}.md"

        d = self._calls
        tool_rate = f"{sum(c.tool_call for c in d) / len(d):.0%}"
        avg_gen = f"{mean(c.gen_tokens for c in d):.0f}"

        summary = (
            "| Model | Turns | Gen (tok/s) | Prompt eval (tok/s)"
            " | Call latency (ms) | Tool-call rate | Avg gen tok |\n"
            "|---|---|---|---|---|---|---|\n"
            f"| `{model_name}` | {len(d)}"
            f" | {_fmt([c.gen_tps for c in d])}"
            f" | {_fmt([c.prompt_tps for c in d])}"
            f" | {_fmt([c.latency_ms for c in d])}"
            f" | {tool_rate}"
            f" | {avg_gen} |\n"
        )

        breakdown = ["| Turn | Latency (ms) | Prompt eval (ms) | Gen (tok/s) | Gen tok | Tool call? |\n",
                     "|---|---|---|---|---|---|\n"]
        for i, c in enumerate(d):
            breakdown.append(
                f"| {i + 1}"
                f" | {c.latency_ms:.0f}"
                f" | {c.prompt_ms:.0f}"
                f" | {c.gen_tps:.1f}"
                f" | {c.gen_tokens}"
                f" | {'✓' if c.tool_call else '—'} |\n"
            )

        md = "\n".join([
            f"# Session — {ts}",
            "",
            f"Model: `{model_name}` · Pi: {'connected' if pi_reachable else 'offline'} · {len(d)} turns",
            "",
            "## Summary",
            "",
            summary,
            "**Metric notes:**",
            "- **Gen (tok/s)** — llama.cpp `predicted_per_second`: generation throughput",
            "- **Prompt eval (tok/s)** — llama.cpp `prompt_per_second`: context ingestion speed",
            "- **Call latency (ms)** — wall-clock time from input sent to response received (LLM only, excludes tool dispatch)",
            "- **Tool-call rate** — fraction of turns that produced ≥1 tool call",
            "- ±N = standard deviation across turns",
            "",
            "## Per-turn latency",
            "",
            "".join(breakdown),
        ])
        out.write_text(md)
        return out
