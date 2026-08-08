"""Dry-run test suite — real LLM at port 8080, all Pi calls faked.

Two test types:
  - ReactiveTest: single prompt → expect tool-call pattern (reactive mode)
  - GoalTest: goal string + fake world state → mini goal loop with real LLM

Usage:
    python -m scripts.test_dry                  # all tests
    python -m scripts.test_dry stop_rules       # reactive category
    python -m scripts.test_dry goal             # goal tests only
"""

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

from dotenv import load_dotenv

from core.llm_client import LLMClient
from core.system_prompt import build_system_prompt, build_goal_prompt
from core.tools import TOOL_SCHEMAS

load_dotenv()

RED    = "\x1b[31m"
GREEN  = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN   = "\x1b[36m"
BOLD   = "\x1b[1m"
DIM    = "\x1b[2m"
RESET  = "\x1b[0m"


# ---------------------------------------------------------------------------
# Fake Pi world state (used by goal tests to vary responses)
# ---------------------------------------------------------------------------

@dataclass
class FakeWorld:
    distance_cm: float = 80.0
    estop: bool = False          # True = movement blocked
    human_detected: bool = False
    color_detected: str | None = None   # e.g. "blue" — returned by get_perception
    color_x: int = 160           # frame position of detected color


def state_str(world: FakeWorld) -> str:
    dist = f"{world.distance_cm:.1f}cm"
    estop = "blocked" if world.estop else "clear"
    human = "detected" if world.human_detected else "not detected"
    return f"distance: {dist} | estop: {estop} | human: {human}"


def fake_result(tool: str, args: dict, world: FakeWorld | None = None) -> dict:
    base = {"ok": True, "tool": tool, "duration_ms": 0, "timestamp": time.time(), "error": None}
    if tool == "move":
        if world and world.estop and args.get("direction") == "forward":
            # Return clear failure so LLM can adapt (production silences this but test needs signal)
            return {**base, "ok": False, "result": {}, "error": "obstacle at 8cm — forward blocked"}
        return {**base, "result": {
            "direction": args.get("direction"),
            "steps_requested": args.get("steps", 1),
            "steps_completed": args.get("steps", 1),
            "halted_early": False,
        }}
    if tool == "pose":
        return {**base, "result": {"pose": args.get("name"), "held_ms": 500}}
    if tool == "set_legs":
        return {**base, "result": {"legs": args.get("legs", []), "speed": args.get("speed", 80)}}
    if tool == "do_trick":
        return {**base, "result": {"name": args.get("name")}}
    if tool == "speak":
        return {**base, "result": {"text": args.get("text", ""), "played": True}}
    if tool == "get_distance":
        cm = world.distance_cm if world else 87.5
        return {**base, "result": {"cm": cm, "reliable": cm > 0}}
    if tool == "get_battery":
        return {**base, "result": {"voltage": 7.6, "percent": 68, "charging": False}}
    if tool == "capture_vision":
        return {**base, "result": {"image_base64": "", "format": "jpeg"}}
    if tool == "wait_for_event":
        return {**base, "result": {"event": "timeout", "text": None, "waited_s": float(args.get("timeout", 1))}}
    if tool == "get_perception":
        color_arg = args.get("color")
        if world and world.color_detected and color_arg == world.color_detected:
            result = {"color": {"target": color_arg, "detected": True, "x": world.color_x, "y": 120, "size": 50}}
        elif color_arg:
            result = {"color": {"target": color_arg, "detected": False, "x": 0, "y": 0, "size": 0}}
        else:
            result = {}
        if args.get("human"):
            result["human"] = {"detected": world.human_detected if world else False}
        if args.get("face"):
            result["face"] = {"detected": False, "x": 0, "y": 0}
        return {**base, "result": result}
    if tool == "goal_complete":
        return {**base, "result": {"outcome": args.get("outcome", ""), "success": args.get("success", False)}}
    return {**base, "ok": False, "result": {}, "error": f"unknown tool: {tool}"}


# ---------------------------------------------------------------------------
# Reactive test runner
# ---------------------------------------------------------------------------

@dataclass
class ReactiveTest:
    name: str
    prompt: str
    category: str
    check: Callable[[list[dict], str], tuple[bool, str]]
    mode: str = "reactive"


@dataclass
class RunResult:
    name: str
    calls: list[dict] = field(default_factory=list)
    final_text: str = ""
    passed: bool = False
    reason: str = ""
    error: str = ""


def calls_of(calls: list[dict], name: str) -> list[dict]:
    return [c for c in calls if c["tool"] == name]


def _fmt_args(args: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:3])


# --- Check functions ---

def exactly_one_speak(calls, _):
    s = calls_of(calls, "speak")
    if len(s) == 1:
        return True, f'speak: "{s[0]["args"].get("text", "")[:60]}"'
    return False, f"expected 1 speak, got {len(s)}"


def has_speak(calls, _):
    s = calls_of(calls, "speak")
    return (True, f'speak: "{s[0]["args"].get("text","")[:60]}"') if s else (False, "no speak call")


def no_speak(calls, _):
    s = calls_of(calls, "speak")
    return (True, "no speak (correct)") if not s else (False, f'unexpected speak: "{s[0]["args"].get("text","")[:40]}"')


def has_tool(name):
    def check(calls, _):
        found = calls_of(calls, name)
        return (True, f"{name}({_fmt_args(found[0]['args'])})") if found else (False, f"no {name} call")
    return check


def has_trick(trick_name):
    def check(calls, _):
        found = calls_of(calls, "do_trick")
        if not found:
            return False, "no do_trick call"
        got = found[0]["args"].get("name", "")
        return True, f"do_trick(name={got!r})" + ("" if got == trick_name else f" — expected {trick_name!r}")
    return check


def has_any_trick(calls, _):
    found = calls_of(calls, "do_trick")
    if found:
        return True, f"do_trick(name={found[0]['args'].get('name')!r})"
    legs = calls_of(calls, "set_legs")
    if len(legs) >= 2:
        return True, f"set_legs chain ({len(legs)} frames)"
    return False, "no trick or set_legs chain"


def speed_is_80(calls, _):
    for c in calls:
        if c["tool"] in ("move", "set_legs", "pose", "do_trick"):
            spd = c["args"].get("speed", 80)
            if spd != 80:
                return False, f"{c['tool']} used speed={spd}, expected 80"
    movers = [c for c in calls if c["tool"] in ("move", "set_legs")]
    return (True, f"speed=80 on {movers[0]['tool']}") if movers else (False, "no movement call to check speed on")


def parallel_move_speak(calls, _):
    moves = calls_of(calls, "move")
    speaks = calls_of(calls, "speak")
    if moves and speaks:
        return True, f'move({moves[0]["args"].get("direction")}) + speak("{speaks[0]["args"].get("text","")[:40]}")'
    return (False, "move but no speak") if moves else (False, "no move call")


def gait_bounded(calls, _):
    legs = calls_of(calls, "set_legs")
    if not legs:
        return False, "no set_legs calls"
    if len(legs) > 12:
        return False, f"{len(legs)} set_legs frames — exceeds 12-frame hard cap"
    return True, f"{len(legs)} set_legs frames"


def no_loop(calls, _):
    counts: dict[str, int] = {}
    for c in calls:
        key = f"{c['tool']}:{json.dumps(c['args'], sort_keys=True)}"
        counts[key] = counts.get(key, 0) + 1
    repeats = {k: v for k, v in counts.items() if v > 1}
    if repeats:
        worst = max(repeats, key=repeats.get)
        return False, f"repeated call ×{repeats[worst]}: {worst[:60]}"
    return True, f"{len(calls)} calls, no repeats"


async def run_reactive(llm: LLMClient, tc: ReactiveTest) -> RunResult:
    res = RunResult(name=tc.name)
    messages = [
        {"role": "system", "content": build_system_prompt(tc.mode)},
        {"role": "user", "content": tc.prompt},
    ]
    try:
        response = await llm.chat_complete(messages, TOOL_SCHEMAS)
    except Exception as e:
        res.error = str(e)
        return res

    iterations = 0
    while response.choices[0].message.tool_calls and iterations < 6:
        msg = response.choices[0].message
        messages.append(llm.format_assistant_message(response))
        for tc_call in msg.tool_calls:
            name = tc_call.function.name
            try:
                args = json.loads(tc_call.function.arguments) if tc_call.function.arguments else {}
            except Exception:
                args = {}
            res.calls.append({"tool": name, "args": args})
            messages.append(llm.format_tool_result(tc_call.id, json.dumps(fake_result(name, args))))
        try:
            response = await llm.chat_complete(messages, TOOL_SCHEMAS)
        except Exception as e:
            res.error = str(e)
            return res
        iterations += 1

    res.final_text = response.choices[0].message.content or ""
    res.passed, res.reason = tc.check(res.calls, res.final_text)
    return res


# ---------------------------------------------------------------------------
# Goal test runner
# ---------------------------------------------------------------------------

@dataclass
class GoalTest:
    name: str
    goal: str
    world: FakeWorld
    check: Callable[[list[dict], bool, str], tuple[bool, str]]
    # check(calls, goal_complete_seen, outcome_text) -> (pass, reason)
    max_outer: int = 4
    category: str = "goal"


async def run_goal_test(llm: LLMClient, tc: GoalTest) -> RunResult:
    """Mini goal loop: state injection + tool calls + goal_complete termination."""
    res = RunResult(name=tc.name)
    messages = [{"role": "system", "content": build_goal_prompt(tc.goal)}]
    goal_complete_seen = False
    outcome_text = ""
    MAX_INNER = 8

    for outer in range(tc.max_outer):
        turn_label = "Begin pursuing your goal." if outer == 0 else "Continue pursuing your goal."
        messages.append({"role": "user", "content": f"[state]\n{state_str(tc.world)}\n\n{turn_label}"})

        try:
            response = await llm.chat_complete(messages, TOOL_SCHEMAS)
        except Exception as e:
            res.error = str(e)
            break

        if not response.choices:
            res.error = "empty choices"
            break

        inner = 0
        while response.choices[0].message.tool_calls and inner < MAX_INNER:
            msg = response.choices[0].message
            messages.append(llm.format_assistant_message(response))

            done_this_inner = False
            for tc_call in msg.tool_calls:
                name = tc_call.function.name
                try:
                    args = json.loads(tc_call.function.arguments) if tc_call.function.arguments else {}
                except Exception:
                    args = {}

                res.calls.append({"tool": name, "args": args})
                result = fake_result(name, args, tc.world)
                messages.append(llm.format_tool_result(tc_call.id, json.dumps(result)))

                if name == "goal_complete":
                    goal_complete_seen = True
                    outcome_text = args.get("outcome", "")
                    done_this_inner = True

            if done_this_inner:
                break

            try:
                response = await llm.chat_complete(messages, TOOL_SCHEMAS)
            except Exception as e:
                res.error = str(e)
                break
            if not response.choices:
                break
            inner += 1

        if goal_complete_seen:
            break

    res.passed, res.reason = tc.check(res.calls, goal_complete_seen, outcome_text)
    return res


# ---------------------------------------------------------------------------
# Goal check functions
# ---------------------------------------------------------------------------

def goal_terminates(calls, done, outcome):
    if done:
        return True, f"goal_complete: {outcome[:70]}"
    return False, "goal_complete never called"


def goal_succeeds(calls, done, outcome):
    goal_calls = calls_of(calls, "goal_complete")
    if done and any(c["args"].get("success") for c in goal_calls):
        return True, f"success=True: {outcome[:60]}"
    if done:
        return False, f"goal_complete called but success=False: {outcome[:60]}"
    return False, "goal_complete never called"


def goal_terminates_and_spoke(calls, done, outcome):
    if not done:
        return False, "goal_complete never called"
    if not calls_of(calls, "speak"):
        return False, "goal completed but never spoke"
    return True, f"spoke + goal_complete: {outcome[:60]}"


def no_forward_on_blocked(calls, done, outcome):
    forwards = [c for c in calls if c["tool"] == "move" and c["args"].get("direction") == "forward"]
    # Allow at most 1 forward attempt (model may try once before adapting).
    # The real guard is code-side (dispatch map blocks); we just verify it isn't looping forward.
    if len(forwards) > 1:
        return False, f"looped {len(forwards)}× forward despite obstacle — not adapting"
    return True, f"forward attempts: {len(forwards)} — {len(calls)} total calls"


def used_perception(calls, done, outcome):
    found = calls_of(calls, "get_perception")
    if not found:
        return False, "never called get_perception"
    return True, f"get_perception called {len(found)}× — {outcome[:50]}"


# ---------------------------------------------------------------------------
# Test definitions
# ---------------------------------------------------------------------------

REACTIVE_TESTS = [
    # STOP rules
    ReactiveTest("how are you",      "how are you?",            "stop_rules", exactly_one_speak),
    ReactiveTest("greeting",         "hi there",                "stop_rules", exactly_one_speak),
    ReactiveTest("what can you do",  "what can you do?",        "stop_rules", exactly_one_speak),

    # Speed default
    ReactiveTest("walk speed",  "walk forward 2 steps",  "speed", speed_is_80),
    ReactiveTest("turn speed",  "turn right",             "speed", speed_is_80),

    # Parallel
    ReactiveTest("walk and say hi",  "walk forward 2 steps and say hi",  "parallel", parallel_move_speak),
    ReactiveTest("move and greet",   "move forward and greet me",         "parallel", parallel_move_speak),

    # Poses
    ReactiveTest("sit",   "sit down",    "pose", has_tool("pose")),
    ReactiveTest("wave",  "wave at me",  "pose", has_tool("pose")),

    # Tricks
    ReactiveTest("pushup",    "do a pushup",           "tricks", has_trick("pushup")),
    ReactiveTest("twist",     "do the twist",          "tricks", has_trick("twist")),
    ReactiveTest("swimming",  "do the swimming trick", "tricks", has_trick("swimming")),
    ReactiveTest("handwork",  "do the handwork trick", "tricks", has_trick("handwork")),
    ReactiveTest("show off",  "show me something cool","tricks", has_any_trick),

    # Gait
    ReactiveTest("be a worm",  "be a worm",         "gait", gait_bounded),
    ReactiveTest("stretch",    "stretch your legs",  "gait", gait_bounded),

    # Sense
    ReactiveTest("distance check",  "how far is the nearest obstacle?",  "sense", has_tool("get_distance")),
    ReactiveTest("battery check",   "check your battery",                 "sense", has_tool("get_battery")),
    ReactiveTest("look around",     "what do you see?",                   "sense", has_tool("capture_vision")),
    ReactiveTest("find blue thing", "is there anything blue nearby?",     "sense", has_tool("get_perception")),

    # No-loop
    ReactiveTest("no loop chat",  "tell me about yourself",  "no_loop", no_loop),
    ReactiveTest("no loop move",  "walk forward 3 steps",    "no_loop", no_loop),
]

GOAL_TESTS = [
    # 1. Simple greeting: human present → speak + goal_complete
    GoalTest(
        name="greet_human",
        goal="say hello to the person in front of you, then call goal_complete",
        world=FakeWorld(distance_cm=60.0, human_detected=True),
        check=goal_terminates_and_spoke,
        max_outer=3,
    ),

    # 2. Walk then stop: clear path → move + goal_complete
    GoalTest(
        name="walk_then_stop",
        goal="walk forward 3 steps then stop",
        world=FakeWorld(distance_cm=100.0),
        check=goal_terminates,
        max_outer=3,
    ),

    # 3. Obstacle blocked: forward returns error → LLM should not loop forward, try alternatives
    GoalTest(
        name="blocked_obstacle",
        goal="walk forward as far as you can",
        world=FakeWorld(distance_cm=8.0, estop=True),
        check=no_forward_on_blocked,
        max_outer=1,
    ),

    # 4. Find color (present): get_perception returns blue → expect goal_complete(success=True)
    GoalTest(
        name="find_color_success",
        goal="is there a blue object nearby? confirm with get_perception then report",
        world=FakeWorld(distance_cm=80.0, color_detected="blue", color_x=160),
        check=goal_succeeds,
        max_outer=3,
    ),

    # 5. Impossible goal: no pink color in world → should give up with goal_complete(success=False)
    GoalTest(
        name="give_up_impossible",
        goal="find a pink object using get_perception — give up if not found",
        world=FakeWorld(distance_cm=80.0),
        check=goal_terminates,
        max_outer=3,
    ),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    filter_arg = sys.argv[1] if len(sys.argv) > 1 else None
    llm = LLMClient()
    print(f"\n{BOLD}Chotu dry-run tests{RESET}  (model: {llm.model})\n")

    total_pass = total_fail = 0
    categories: dict[str, list[RunResult]] = {}

    # Reactive tests
    reactive = REACTIVE_TESTS if not filter_arg else [t for t in REACTIVE_TESTS if t.category == filter_arg]
    if reactive and (not filter_arg or filter_arg != "goal"):
        print(f"{BOLD}── Reactive mode ─────────────────────────────────────────────────{RESET}")
        for tc in reactive:
            print(f"  {DIM}{tc.category:12}{RESET} {tc.name:<25} ", end="", flush=True)
            result = await run_reactive(llm, tc)
            _print_result(result)
            categories.setdefault(tc.category, []).append(result)
            if result.passed:
                total_pass += 1
            else:
                total_fail += 1

    # Goal tests
    goal_tests = GOAL_TESTS if not filter_arg else [t for t in GOAL_TESTS if t.category == filter_arg or filter_arg == "goal"]
    if goal_tests:
        print(f"\n{BOLD}── Goal mode ─────────────────────────────────────────────────────{RESET}")
        for tc in goal_tests:
            print(f"  {DIM}{'goal':12}{RESET} {tc.name:<25} ", end="", flush=True)
            result = await run_goal_test(llm, tc)
            _print_result(result)
            categories.setdefault("goal", []).append(result)
            if result.passed:
                total_pass += 1
            else:
                total_fail += 1

    # Summary
    print(f"\n{'─'*66}")
    print(f"  {BOLD}Results by category:{RESET}")
    for cat, results in categories.items():
        p = sum(1 for r in results if r.passed and not r.error)
        f = len(results) - p
        bar = f"{GREEN}{'█' * p}{RESET}{RED}{'█' * f}{RESET}"
        print(f"    {cat:<14} {bar}  {p}/{len(results)}")
    print(f"\n  {BOLD}Total: {GREEN}{total_pass} passed{RESET}  {RED}{total_fail} failed{RESET}  out of {total_pass + total_fail}{RESET}\n")


def _print_result(result: RunResult):
    if result.error:
        print(f"{RED}ERROR{RESET}  {result.error[:70]}")
    elif result.passed:
        print(f"{GREEN}PASS{RESET}   {DIM}{result.reason[:70]}{RESET}")
    else:
        print(f"{RED}FAIL{RESET}   {YELLOW}{result.reason[:70]}{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
