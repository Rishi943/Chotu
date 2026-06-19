"""Dry-run brain harness — model-comparison rig.

Faithful to the REAL brain loop (core.brain.run_iteration): one LLM call per tick,
all tool calls in that response dispatched (deduped) in one tick, a frame pushed
between ticks, context compacted. Pi calls and the camera are faked. Reuses the
brain's own build_loop_messages / Scratchpad / frame_stack / split_tool_calls so
what you see here is what the robot would do.

Measures, per scenario:
  - tool-call correctness   (right tools, valid args, no native-format leakage)
  - persona / voice         (monologue + speak lines shown prominently)
  - latency / tokens        (prompt/completion tokens, tok/s, wall time)
  - instruction-following   (per-tick `expect`, per-scenario `forbid` checks)

Usage:
    python -m scripts.robot.dry_run                          # list scenarios
    python -m scripts.robot.dry_run --scenario reel          # run reel.json (placeholder frames)
    python -m scripts.robot.dry_run --scenario reel --images scripts/test_frames/reel
    python -m scripts.robot.dry_run "walk forward 2 steps"   # ad-hoc single-tick prompt
    PALIV_MUTE=1 PALIV_BRAIN_MODEL=gemma-4-E4B.Q4_K_M.gguf python -m scripts.robot.dry_run --scenario reel
"""

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from core.llm_client import LLMClient
from core.prompts import SYSTEM_PROMPT
from core.tools import TOOL_SCHEMAS
from core.loop_helpers import (
    motion_from_calls, push_frame, maybe_compact, cap_result, split_tool_calls,
)
from core.scratchpad import Scratchpad
from core.brain import build_loop_messages  # the real message builder

load_dotenv()

MUTE = os.getenv("PALIV_MUTE", "0") == "1"
COMPACT_AT_TOKENS = int(os.getenv("PALIV_COMPACT_AT_TOKENS", "10000"))
COMPACT_KEEP_TOKENS = int(os.getenv("PALIV_COMPACT_KEEP_TOKENS", "6000"))

SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios"
TESTOUT_DIR = Path(__file__).resolve().parents[2] / "out"

# ANSI
DIM, BOLD, CYAN, YEL, GRN, RED, MAG = (
    "\x1b[90m", "\x1b[1m", "\x1b[36m", "\x1b[1;33m", "\x1b[32m", "\x1b[31m", "\x1b[35m",
)
RST = "\x1b[0m"

# Native-tool-call syntax that leaked into the content field instead of being parsed
# into tool_calls. Gemma's leak looked like: call:move{...<|"|>forward<|"|>...}.
_LEAK_RE = re.compile(r"call:\s*\w+\s*\{|<\|.*?\|>|<tool_call>|```tool", re.IGNORECASE)

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


# PALIV.md / CHOTU_BASE.md are stale vs the current always-on-vision, dead-sensor
# reality: they tell the model to call `capture_vision` (a tool that no longer exists —
# vision is now captured automatically every tick) and to `get_distance` before moving
# (sensor returns -1). Following them, the model reaches for a non-existent look tool and
# falls back to get_distance reflexively. These fixes modernize the prompt IN THE HARNESS
# ONLY so we can measure the model without the stale-prompt confound. The real .md files
# need the same treatment before adopting any model — see chotu_deferred_behaviour memory.
_PROMPT_FIXES = [
    # get_distance directives (dead sensor)
    (" Turn first, then check distance.", " Turn first."),
    (" Use `get_distance()` first if you suspect an obstacle.", ""),
    # capture_vision references (tool removed; vision is always-on)
    ("You have no continuous perception — what you see, you saw because you called `capture_vision`.",
     "You have a live camera feed — the current view is always in front of you."),
    ("2. Haven't looked around recently? `capture_vision`. Dark room counts — darkness is information.",
     "2. Something in your current view worth investigating? Move toward it. Darkness is information too."),
    ("- Don't loop on `capture_vision` — one look per turn, then describe. Stop.\n", ""),
    ("- `capture_vision()` — forward camera photo, injected as deferred user-message after all tool results in the same turn.\n", ""),
]


# E4B can't fully disable thinking (model card #2: E2B/E4B are the exception); with
# --reasoning-budget 0 it just moves chain-of-thought into the visible content field
# (300-400 tokens/tick). In the brain that monologue isn't spoken — it's pure latency.
# This directive curbs it so generation stays short enough to finish within a tool call.
_TERSE_DIRECTIVE = (
    "\n\n## Respond tersely\n"
    "Reason silently — do NOT write your analysis, numbered plans, or phrases like "
    "\"The user is asking\" in the content field. Lead with tool calls. Put at most one "
    "short sentence in content (or leave it empty). Speak at most one short line."
)


def scrub_stale_prompt(prompt: str, terse: bool = True) -> str:
    for old, new in _PROMPT_FIXES:
        prompt = prompt.replace(old, new)
    if terse:
        prompt += _TERSE_DIRECTIVE
    return prompt


def fake_result(tool: str, args: dict) -> dict:
    """Fake a Pi envelope for the current 8 brain tools."""
    base = {"ok": True, "tool": tool, "duration_ms": 0, "timestamp": time.time(), "error": None}
    if tool == "move":
        return {**base, "result": {
            "direction": args.get("direction", "forward"),
            "steps_requested": args.get("steps", 1),
            "steps_completed": args.get("steps", 1),
            "halted_early": False,
        }}
    if tool == "pose":
        return {**base, "result": {"pose": args.get("name", "stand"), "held_ms": 500}}
    if tool == "get_distance":
        return {**base, "result": {"cm": -1, "reliable": False}}  # matches real (dead sensor)
    if tool == "get_battery":
        return {**base, "result": {"voltage": 7.6, "percent": 68, "charging": True}}
    if tool == "set_face":
        return {**base, "result": {"name": args.get("name", "idle"), "ok": True}}
    if tool == "wait":
        return {**base, "result": {"waited_seconds": args.get("seconds", 1), "reason": args.get("reason", "")}}
    if tool == "cast_spell":
        return {**base, "result": {"spell": args.get("name", "")}}
    if tool == "speak":
        return {**base, "result": {"text": args.get("text", ""), "played": True}}
    return {**base, "ok": False, "result": {}, "error": f"unknown tool: {tool}"}


def load_image_b64(images_dir: Path | None, name: str | None) -> str | None:
    if not name or images_dir is None:
        return None
    p = images_dir / name
    if not p.exists():
        print(f"  {DIM}[frame missing: {p} — running this tick blind]{RST}")
        return None
    return base64.b64encode(p.read_bytes()).decode("ascii")


# --- per-run metrics ---

class Metrics:
    def __init__(self):
        self.ticks = 0
        self.tool_calls = 0
        self.leaks = 0
        self.forbidden_hits = 0
        self.expect_hits = 0
        self.expect_total = 0
        self.prompt_tok = 0
        self.completion_tok = 0
        self.wall = 0.0
        # server-reported timings (ms / token counts), summed across ticks — these
        # separate prompt-eval from generation so "gen t/s" isn't polluted by prefill.
        self.prompt_ms = 0.0
        self.prompt_n = 0
        self.gen_ms = 0.0
        self.gen_n = 0

    def report(self, model: str):
        print(f"\n{BOLD}══ summary: {model} ══{RST}")
        print(f"  ticks: {self.ticks}   tool calls: {self.tool_calls}")
        leak = f"{RED}{self.leaks}{RST}" if self.leaks else f"{GRN}0{RST}"
        print(f"  format leaks: {leak}")
        if self.expect_total:
            rate = 100 * self.expect_hits / self.expect_total
            col = GRN if rate >= 80 else (YEL if rate >= 50 else RED)
            print(f"  expected-tool hits: {col}{self.expect_hits}/{self.expect_total} ({rate:.0f}%){RST}")
        fb = f"{RED}{self.forbidden_hits}{RST}" if self.forbidden_hits else f"{GRN}0{RST}"
        print(f"  forbidden-tool calls: {fb}")
        tot = self.prompt_tok + self.completion_tok
        print(f"  tokens: {tot} ({self.prompt_tok}p + {self.completion_tok}c)   wall: {self.wall:.1f}s")
        # Real per-phase speeds from server timings. Prompt eval (prefill) is where image
        # tokens cost; generation is the decode speed. Conflating them hides the truth.
        if self.gen_ms > 0:
            gen_tps = self.gen_n / (self.gen_ms / 1000)
            prompt_tps = self.prompt_n / (self.prompt_ms / 1000) if self.prompt_ms else 0
            print(f"  prompt eval: {self.prompt_n} tok in {self.prompt_ms/1000:.1f}s ({prompt_tps:.0f} tok/s)")
            print(f"  generation:  {self.gen_n} tok in {self.gen_ms/1000:.1f}s ({gen_tps:.1f} tok/s)")


async def run_tick(llm, memory, frame_stack, scratch, tick, prev_motion, m: Metrics, text_last: bool, system_prompt: str, records: list):
    """Faithful single tick. Returns this tick's motion_desc (for next frame label)."""
    user_input = tick.get("input")
    image_b64 = tick.get("_image_b64")
    expect = tick.get("expect") or []
    forbid = tick.get("_forbid") or []

    # transcript record for this tick (written to the .md at end of run)
    rec = {"note": tick.get("note"), "input": user_input, "monologue": "",
           "think": [], "spoken": [], "tools": [], "missing": [],
           "forbidden": [], "leak": None, "error": None, "stats": None}
    records.append(rec)

    if tick.get("note"):
        print(f"\n{DIM}# {tick['note']}{RST}")
    if user_input:
        print(f"{YEL}user>{RST} {user_input}")

    # raw mode (e.g. Qwen baseline): replicate the brain's real ordering — current input
    # lands in memory BEFORE the frames, frames are the last messages.
    if not text_last and user_input:
        memory.append({"role": "user", "content": f"[human] {user_input}", "_origin": "user"})

    # Frame the camera "sees" this tick → becomes frame 0 (NOW). Records prev tick's motion
    # on the previously-current frame, exactly like the brain's push_frame.
    if image_b64:
        push_frame(frame_stack, image_b64, prev_motion)

    messages = build_loop_messages(
        system_prompt, memory, frame_stack, scratch,
        cache_boundary=llm.supports_cache_control,
    )
    # Gemma-friendly layout (text_last): keep [system | prior memory] as a stable cached
    # prefix (current input NOT in it yet), let the frame stay in the volatile tail, and
    # put the current turn's TEXT after the frame so it can attend to it (image-before-text).
    # On a no-input heartbeat, a nudge plays that role.
    if text_last:
        trailing = f"[human] {user_input}" if user_input else "[heartbeat] What do you do now?"
        messages = messages + [{"role": "user", "content": trailing}]

    t0 = time.time()
    try:
        resp = await llm.chat_complete(messages, TOOL_SCHEMAS)
    except Exception as e:
        print(f"  {RED}LLM error: {e}{RST}")
        rec["error"] = str(e)
        return prev_motion
    dt = time.time() - t0
    m.wall += dt
    m.ticks += 1

    if resp.usage:
        m.prompt_tok += resp.usage.get("prompt_tokens", 0)
        m.completion_tok += resp.usage.get("completion_tokens", 0)
        # llama.cpp server timings split prefill vs decode — the only honest speed numbers.
        tm = resp.usage.get("timings") or {}
        m.prompt_ms += tm.get("prompt_ms", 0.0)
        m.prompt_n += tm.get("prompt_n", 0)
        m.gen_ms += tm.get("predicted_ms", 0.0)
        m.gen_n += tm.get("predicted_n", 0)
        rec["stats"] = {
            "ptok": resp.usage.get("prompt_tokens", 0),
            "ctok": resp.usage.get("completion_tokens", 0),
            "dt": dt,
            "prompt_ms": tm.get("prompt_ms", 0.0),
            "prompt_tps": tm.get("prompt_per_second", 0.0),
            "gen_tps": tm.get("predicted_per_second", 0.0),
            "gen_n": tm.get("predicted_n", 0),
        }
        speeds = ""
        if tm.get("predicted_ms"):
            pe = f"{tm['prompt_ms']/1000:.1f}s@{tm.get('prompt_per_second',0):.0f}t/s" if tm.get("prompt_ms") else "cached"
            speeds = f"  prefill {pe} · gen {tm.get('predicted_per_second',0):.0f}t/s"
        print(f"  {DIM}[{resp.usage.get('prompt_tokens',0)}p/"
              f"{resp.usage.get('completion_tokens',0)}c  {dt:.1f}s{speeds}]{RST}")

    msg = resp.choices[0].message
    content = msg.content or ""
    think = _THINK_RE.findall(content)
    clean = _THINK_RE.sub("", content).strip()
    rec["think"] = [t.strip() for t in think if t.strip()]
    for t in think:
        if t.strip():
            print(f"  {DIM}[think] {t.strip()[:140]}{RST}")

    # format-leak detection: native tool syntax left in content
    if _LEAK_RE.search(clean):
        m.leaks += 1
        rec["leak"] = clean
        print(f"  {RED}[FORMAT LEAK] native tool-call syntax in content:{RST}")
        print(f"    {RED}{clean[:200]}{RST}")
    elif clean:
        rec["monologue"] = clean
        print(f"  {MAG}[monologue]{RST} {clean}")

    # text_last mode: now persist the current input into memory (it was kept out of the
    # cached prefix for this call) so it's prior context on the next tick — placed before
    # the assistant turn it prompted.
    if text_last and user_input:
        memory.append({"role": "user", "content": f"[human] {user_input}", "_origin": "user"})

    # assistant turn into memory (mirror brain)
    am = llm.format_assistant_message(resp)
    am["content"] = clean
    am["_origin"] = "loop"
    memory.append(am)

    motion_desc = prev_motion
    called = []
    if msg.tool_calls:
        keep, suppressed = split_tool_calls(msg.tool_calls)
        state_calls = []
        for tc in keep:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            res = fake_result(name, args)
            called.append(name)
            m.tool_calls += 1
            if name == "speak":
                label = "muted" if MUTE else "speaks"
                rec["spoken"].append((args.get("text") or "").strip())
                print(f'  {CYAN}[{label}]{RST} "{(args.get("text") or "").strip()}"')
            else:
                ok = "ok" if res.get("ok") else f"{RED}FAIL: {res.get('error')}{RST}"
                astr = ", ".join(f"{k}={v!r}" for k, v in args.items())
                rec["tools"].append((name, args))
                print(f"  {GRN}[{name}]{RST} {astr} -> {ok}")
            if forbid and name in forbid:
                m.forbidden_hits += 1
                rec["forbidden"].append(name)
                print(f"    {RED}↑ FORBIDDEN by scenario{RST}")
            memory.append(llm.format_tool_result(tc.id, cap_result(json.dumps(res))))
            state_calls.append((name, args, res))
        for tc in suppressed:
            env = {"ok": True, "tool": tc.function.name, "result": {"suppressed": True},
                   "duration_ms": 0, "timestamp": time.time(), "error": None}
            memory.append(llm.format_tool_result(tc.id, cap_result(json.dumps(env))))
            print(f"  {DIM}[suppressed dup] {tc.function.name}{RST}")
        motion_desc = motion_from_calls([(n, a) for n, a, _ in state_calls])
        scratch.update(state_calls)

    # expect check
    if expect:
        hit = sum(1 for e in expect if e in called)
        m.expect_hits += hit
        m.expect_total += len(expect)
        missing = [e for e in expect if e not in called]
        rec["missing"] = missing
        if missing:
            print(f"  {YEL}[expected but not called: {', '.join(missing)}]{RST}")

    maybe_compact(memory, COMPACT_AT_TOKENS, COMPACT_KEEP_TOKENS)
    return motion_desc


def write_transcript(scenario_name: str, model: str, text_last: bool,
                     prompt_label: str, records: list, m: Metrics) -> Path:
    """Write the run's dialogue + metrics to out/<scenario>_<date>_<time>.md."""
    TESTOUT_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "", scenario_name.lower()) or "run"
    stamp = time.strftime("%Y-%m-%d_%H-%M")
    path = TESTOUT_DIR / f"{slug}_{stamp}.md"

    L = [f"# {scenario_name} — {stamp}", ""]
    L.append(f"Model: `{model}` · text_last: {'on' if text_last else 'off'} · "
             f"prompt: {prompt_label} · {m.ticks} ticks")
    L += ["", "## Transcript", ""]
    for i, r in enumerate(records, 1):
        head = f"### Tick {i}" + (f" — {r['note']}" if r["note"] else "")
        L.append(head)
        if r["input"]:
            L.append(f"- **input:** {r['input']}")
        if r["error"]:
            L.append(f"- **LLM error:** {r['error']}")
        for t in r["think"]:
            L.append(f"- **think:** {t}")
        if r["monologue"]:
            L.append(f"- **monologue:** {r['monologue']}")
        for s in r["spoken"]:
            L.append(f'- **speak:** "{s}"')
        for name, args in r["tools"]:
            astr = ", ".join(f"{k}={v}" for k, v in args.items())
            L.append(f"- **tool:** `{name}({astr})`")
        if r["leak"]:
            L.append(f"- **⚠ format leak:** {r['leak'][:200]}")
        if r["forbidden"]:
            L.append(f"- **⚠ forbidden:** {', '.join(r['forbidden'])}")
        if r["missing"]:
            L.append(f"- **expected but not called:** {', '.join(r['missing'])}")
        if not any((r["input"], r["monologue"], r["spoken"], r["tools"], r["error"])):
            L.append("- *(no output)*")
        L.append("")

    L += ["## Summary", ""]
    tot = m.prompt_tok + m.completion_tok
    L.append(f"- ticks: {m.ticks} · tool calls: {m.tool_calls}")
    L.append(f"- format leaks: {m.leaks}")
    if m.expect_total:
        L.append(f"- expected-tool hits: {m.expect_hits}/{m.expect_total} "
                 f"({100 * m.expect_hits / m.expect_total:.0f}%)")
    L.append(f"- forbidden-tool calls: {m.forbidden_hits}")
    L.append(f"- tokens: {tot} ({m.prompt_tok}p + {m.completion_tok}c) · wall: {m.wall:.1f}s")
    if m.gen_ms > 0:
        gen_tps = m.gen_n / (m.gen_ms / 1000)
        prompt_tps = m.prompt_n / (m.prompt_ms / 1000) if m.prompt_ms else 0
        L.append(f"- prompt eval: {m.prompt_n} tok in {m.prompt_ms / 1000:.1f}s ({prompt_tps:.0f} tok/s)")
        L.append(f"- generation: {m.gen_n} tok in {m.gen_ms / 1000:.1f}s ({gen_tps:.1f} tok/s)")

    L += ["", "## Per-turn", "",
          "| Tick | Latency (ms) | Prompt eval (tok/s) | Gen (tok/s) | Gen tok | Tools |",
          "|---|---|---|---|---|---|"]
    for i, r in enumerate(records, 1):
        s = r["stats"]
        ntools = len(r["tools"]) + len(r["spoken"])
        if s:
            L.append(f"| {i} | {s['dt'] * 1000:.0f} | {s['prompt_tps']:.0f} | "
                     f"{s['gen_tps']:.0f} | {s['gen_n']} | {ntools} |")
        else:
            L.append(f"| {i} | — | — | — | — | {ntools} |")
    L.append("")

    path.write_text("\n".join(L), encoding="utf-8")
    return path


async def run_scenario(llm, scenario: dict, images_dir: Path | None, text_last: bool,
                       system_prompt: str, prompt_label: str):
    print(f"\n{BOLD}▶ scenario: {scenario['name']}{RST}  ({llm.model})")
    if scenario.get("description"):
        print(f"{DIM}{scenario['description']}{RST}")
    forbid = scenario.get("forbid") or []

    memory: list[dict] = []
    frame_stack: list[dict] = []
    scratch = Scratchpad()
    m = Metrics()
    records: list = []
    prev_motion = "no movement"

    for tick in scenario["ticks"]:
        tick = dict(tick)
        tick["_image_b64"] = load_image_b64(images_dir, tick.get("image"))
        tick["_forbid"] = forbid
        prev_motion = await run_tick(llm, memory, frame_stack, scratch, tick, prev_motion, m, text_last, system_prompt, records)

    m.report(llm.model)
    path = write_transcript(scenario["name"], llm.model, text_last, prompt_label, records, m)
    print(f"{DIM}[transcript saved: {path}]{RST}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="*", help="ad-hoc single-tick prompt")
    ap.add_argument("--scenario", "-s", help="scenario name (file in scripts/scenarios/)")
    ap.add_argument("--images", help="folder of test frames referenced by the scenario")
    ap.add_argument("--no-text-last", action="store_true",
                    help="disable the Gemma image-last tool-parse workaround (test raw brain ordering)")
    ap.add_argument("--raw-prompt", action="store_true",
                    help="use the unmodified PALIV/CHOTU prompt (default: scrub stale capture_vision + get_distance refs)")
    ap.add_argument("--no-terse", action="store_true",
                    help="don't append the terse-output directive (default: appended, to curb E4B in-content reasoning)")
    args = ap.parse_args()

    llm = LLMClient()
    images_dir = Path(args.images) if args.images else None
    text_last = not args.no_text_last
    if args.raw_prompt:
        system_prompt = SYSTEM_PROMPT
        prompt_label = "raw"
    else:
        system_prompt = scrub_stale_prompt(SYSTEM_PROMPT, terse=not args.no_terse)
        prompt_label = "scrubbed" if args.no_terse else "scrubbed+terse"
        extra = "" if args.no_terse else " + terse directive"
        print(f"{DIM}[system prompt: stale capture_vision + get_distance refs scrubbed{extra}]{RST}")

    if args.scenario:
        path = SCENARIO_DIR / f"{args.scenario}.json"
        if not path.exists():
            print(f"no such scenario: {path}")
            return
        scenario = json.loads(path.read_text())
        await run_scenario(llm, scenario, images_dir, text_last, system_prompt, prompt_label)
    elif args.prompt:
        scenario = {"name": "ad-hoc", "ticks": [{"input": " ".join(args.prompt)}]}
        await run_scenario(llm, scenario, images_dir, text_last, system_prompt, prompt_label)
    else:
        print(f"{BOLD}Scenarios in {SCENARIO_DIR}:{RST}")
        for p in sorted(SCENARIO_DIR.glob("*.json")):
            try:
                d = json.loads(p.read_text())
                print(f"  {p.stem:12} — {d.get('description','')}")
            except Exception:
                print(f"  {p.stem:12} — (unreadable)")
        print(f"\nrun:  python -m scripts.robot.dry_run --scenario <name> [--images <dir>]")

    await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
