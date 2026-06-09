# Cache Breakpoints + Token-Pressure Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the DashScope cache breakpoint to the end of the stable `memory` prefix (plus a system floor), port caching + param threading to the Claude path, and switch compaction from a turn-count trigger to a memory-token trigger with token-aware trimming.

**Architecture:** A provider-agnostic boundary tag (`_cache_boundary`) is placed on the last `memory` message by `build_loop_messages`, gated by a new `LLMClient.supports_cache_control` property so the internal tag never leaks to llama-server. Each provider path marks its own `cache_control` syntax (OpenAI block-wrap for local/DashScope, system-list + tool_result block for Claude). Compaction triggers on a `chars//4` memory-token estimate and drops whole oldest turns until under a keep target.

**Tech Stack:** Python 3.12, pytest (`asyncio_mode = auto`), OpenAI async SDK (llama-server / DashScope), Anthropic async SDK (fallback).

**Spec:** `docs/superpowers/specs/2026-06-09-cache-breakpoints-token-compaction-design.md`

**Sequencing note:** Tasks 1–6 are local-verifiable (free) and land first. Task 7 only *adds* the cloud probe harness; it is **not run** — running it needs explicit per-instance approval (`CLAUDE.md` cloud-token rule).

---

## Task 1: Token-aware compaction helpers

**Files:**
- Modify: `core/loop_helpers.py` (replace `maybe_compact` at lines 91–99; add `estimate_memory_tokens`)
- Test: `tests/test_loop_helpers.py` (replace the 3 `maybe_compact` tests at lines 203–231, end of file)

- [ ] **Step 1: Replace the old turn-count `maybe_compact` tests with token-based tests**

In `tests/test_loop_helpers.py`, replace everything from line 203 (`from core.loop_helpers import maybe_compact`) to end of file with:

```python
from core.loop_helpers import maybe_compact, estimate_memory_tokens


def _turn(i, content_chars=0):
    """One assistant+tool turn. content_chars pads the tool result to size it."""
    return [
        {"role": "assistant", "content": f"think {i}"},
        {"role": "tool", "tool_call_id": str(i), "content": "x" * content_chars},
    ]


def _mem(n, content_chars=0):
    mem = []
    for i in range(n):
        mem.extend(_turn(i, content_chars))
    return mem


def test_estimate_memory_tokens_counts_string_content():
    mem = [{"role": "user", "content": "a" * 400}]
    assert estimate_memory_tokens(mem) == 100  # 400 chars // 4


def test_estimate_memory_tokens_counts_tool_call_arguments():
    mem = [{"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "move", "arguments": "x" * 396}}]}]
    assert estimate_memory_tokens(mem) == 100  # (4 name + 396 args) // 4


def test_estimate_memory_tokens_counts_list_content_text():
    mem = [{"role": "user", "content": [{"type": "text", "text": "b" * 800}]}]
    assert estimate_memory_tokens(mem) == 200


def test_maybe_compact_noop_below_threshold():
    mem = _mem(10, content_chars=40)            # ~ small
    before = list(mem)
    maybe_compact(mem, at_tokens=100_000, keep_tokens=6_000)
    assert mem == before                        # append-only: untouched


def test_maybe_compact_trims_whole_turns_to_under_keep():
    # 20 turns, each tool result 4000 chars => ~1000 tok/turn => ~20k tok total
    mem = _mem(20, content_chars=4000)
    maybe_compact(mem, at_tokens=10_000, keep_tokens=6_000)
    assert estimate_memory_tokens(mem) <= 6_000
    # turn alignment preserved: still starts on an assistant, no orphan tool result
    assert mem[0]["role"] == "assistant"
    assert sum(1 for m in mem if m["role"] == "assistant") >= 1


def test_maybe_compact_keeps_at_least_last_turn_when_single_turn_exceeds_keep():
    mem = _mem(5, content_chars=40_000)         # each turn ~10k tok, over keep alone
    maybe_compact(mem, at_tokens=10_000, keep_tokens=6_000)
    # cannot get under keep without dropping the last turn — keep exactly the last one
    assert sum(1 for m in mem if m["role"] == "assistant") == 1
    assert mem[0] == {"role": "assistant", "content": "think 4"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_loop_helpers.py -k "estimate_memory_tokens or maybe_compact" -v`
Expected: FAIL — `ImportError: cannot import name 'estimate_memory_tokens'`.

- [ ] **Step 3: Implement `estimate_memory_tokens` and rewrite `maybe_compact`**

In `core/loop_helpers.py`, replace the existing `maybe_compact` function (lines 91–99) with:

```python
def estimate_memory_tokens(memory: list[dict]) -> int:
    """chars/4 over message text + tool_call argument lengths. Memory is text-only
    (frames live in the volatile tail), so no image accounting is needed."""
    chars = 0
    for m in memory:
        c = m.get("content")
        if isinstance(c, str):
            chars += len(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    chars += len(b.get("text") or b.get("content") or "")
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            chars += len(fn.get("name", "")) + len(fn.get("arguments", "") or "")
    return chars // 4


def maybe_compact(memory: list[dict], at_tokens: int, keep_tokens: int) -> None:
    """No-op while memory is under at_tokens. Above it, drop whole oldest turns
    (aligned to assistant starts, so no orphaned tool results) until the remainder
    is <= keep_tokens, keeping at least the last turn. Mutates memory."""
    if estimate_memory_tokens(memory) <= at_tokens:
        return
    starts = [i for i, m in enumerate(memory) if m.get("role") == "assistant"]
    for s in starts:
        if estimate_memory_tokens(memory[s:]) <= keep_tokens:
            del memory[:s]
            return
    if starts:  # even the last turn exceeds keep — keep it alone
        del memory[:starts[-1]]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_loop_helpers.py -k "estimate_memory_tokens or maybe_compact" -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add core/loop_helpers.py tests/test_loop_helpers.py
git commit -m "feat(loop): token-aware compaction — estimate_memory_tokens + maybe_compact (T2.3)"
```

---

## Task 2: Wire token compaction constants + call site in brain.py

**Files:**
- Modify: `core/brain.py` (constants at lines 38–39; `maybe_compact` import at line 22; call site at line 347)

- [ ] **Step 1: Replace the compaction constants**

In `core/brain.py`, replace lines 38–39:

```python
COMPACT_AT = int(os.getenv("PALIV_COMPACT_AT", "30"))    # append-only until this many assistant turns accumulate
COMPACT_KEEP = int(os.getenv("PALIV_COMPACT_KEEP", "8")) # turns retained after a compaction
```

with:

```python
COMPACT_AT_TOKENS   = int(os.getenv("PALIV_COMPACT_AT_TOKENS", "10000"))   # est. memory tokens that trigger a trim
COMPACT_KEEP_TOKENS = int(os.getenv("PALIV_COMPACT_KEEP_TOKENS", "6000"))  # est. memory tokens retained after a trim
```

- [ ] **Step 2: Update the call site**

In `core/brain.py` line 347, replace:

```python
    maybe_compact(memory, COMPACT_AT, COMPACT_KEEP)
```

with:

```python
    maybe_compact(memory, COMPACT_AT_TOKENS, COMPACT_KEEP_TOKENS)
```

(The `maybe_compact` import on line 22 stays — only the args change.)

- [ ] **Step 3: Verify brain.py imports cleanly**

Run: `python -c "import core.brain"`
Expected: no error (no output, exit 0).

- [ ] **Step 4: Commit**

```bash
git add core/brain.py
git commit -m "feat(loop): trigger compaction on memory-token pressure, not turn count (T2.3)"
```

---

## Task 3: Boundary plumbing — `supports_cache_control` + `build_loop_messages` tag

**Files:**
- Modify: `core/llm_client.py` (add property near line 92)
- Modify: `core/brain.py` (`build_loop_messages` signature/body at lines 92–104; call site at line 266)
- Test: `tests/test_run_iteration.py` (append a new test); `tests/test_llm_client.py` (create)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run_iteration.py`:

```python
def test_build_loop_messages_tags_last_memory_when_cache_boundary():
    from core.brain import build_loop_messages
    from core.scratchpad import Scratchpad
    memory = [
        {"role": "assistant", "content": "a", "_origin": "loop"},
        {"role": "tool", "tool_call_id": "1", "content": "{}"},
    ]
    msgs = build_loop_messages("SYS", memory, [], Scratchpad(), cache_boundary=True)
    # the tool result (last memory msg) carries the boundary tag; nothing else does
    tagged = [m for m in msgs if m.get("_cache_boundary")]
    assert len(tagged) == 1
    assert tagged[0]["role"] == "tool"


def test_build_loop_messages_no_tag_when_cache_boundary_false():
    from core.brain import build_loop_messages
    from core.scratchpad import Scratchpad
    memory = [{"role": "assistant", "content": "a"}]
    msgs = build_loop_messages("SYS", memory, [], Scratchpad())  # default False
    assert all("_cache_boundary" not in m for m in msgs)
```

Create `tests/test_llm_client.py`:

```python
import os
from core.llm_client import LLMClient


def _client(provider, url=""):
    os.environ["PALIV_LLM_PROVIDER"] = provider
    if url:
        os.environ["PALIV_BRAIN_URL"] = url
    elif "PALIV_BRAIN_URL" in os.environ:
        del os.environ["PALIV_BRAIN_URL"]
    return LLMClient()


def test_supports_cache_control_local_dashscope_true():
    c = _client("local", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    assert c.supports_cache_control is True


def test_supports_cache_control_local_llama_false():
    c = _client("local", "http://localhost:8080/v1")
    assert c.supports_cache_control is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_run_iteration.py tests/test_llm_client.py -v`
Expected: FAIL — `build_loop_messages() got an unexpected keyword argument 'cache_boundary'` and `AttributeError: ... supports_cache_control`.

- [ ] **Step 3: Add the `supports_cache_control` property**

In `core/llm_client.py`, add immediately after `chat_complete` (after line 113, before `format_assistant_message`):

```python
    @property
    def supports_cache_control(self) -> bool:
        """True when the active provider honors cache_control markers: Claude always,
        local only when pointed at DashScope. Gates the _cache_boundary tag so it never
        reaches llama-server (which would reject the unknown field)."""
        return self.provider == "claude" or self._cache_system
```

Note: `self._cache_system` is only set in the `local` branch of `__init__`. Add `self._cache_system = False` in the `claude` branch (after line 86, `self._openai = None`) so the property is safe on every provider.

- [ ] **Step 4: Update `build_loop_messages`**

In `core/brain.py`, replace the body of `build_loop_messages` (lines 92–104) with:

```python
def build_loop_messages(system_prompt: str, memory: list[dict], frame_stack: list[dict],
                        scratchpad: "Scratchpad", cache_boundary: bool = False) -> list[dict]:
    """System prompt + append-only window + state block + the 3 motion-labeled frames.
    Order is [system | memory | STATE | frames]: system+memory are the stable cached
    prefix; STATE and frames are the small volatile tail. Internal `_origin` fields are
    stripped so the result is safe to send to the LLM. When `cache_boundary` is set, the
    last memory message is tagged `_cache_boundary` for the provider to mark cache_control."""
    msgs = [{"role": "system", "content": system_prompt}]
    mem_msgs = strip_internal_fields(memory)
    if cache_boundary and mem_msgs:
        mem_msgs[-1] = {**mem_msgs[-1], "_cache_boundary": True}
    msgs.extend(mem_msgs)
    state = scratchpad.render()
    if state is not None:
        msgs.append({k: v for k, v in state.items() if not k.startswith("_")})
    msgs.extend(render_frames(frame_stack))
    return msgs
```

- [ ] **Step 5: Update the call site**

In `core/brain.py` line 266, replace:

```python
    messages = build_loop_messages(SYSTEM_PROMPT, memory, frame_stack, scratchpad)
```

with:

```python
    messages = build_loop_messages(
        SYSTEM_PROMPT, memory, frame_stack, scratchpad,
        cache_boundary=llm_client.supports_cache_control,
    )
```

- [ ] **Step 6: Run to verify pass**

Run: `python -m pytest tests/test_run_iteration.py tests/test_llm_client.py -v`
Expected: PASS (all tests, including the 2 pre-existing `build_loop_messages` tests).

- [ ] **Step 7: Commit**

```bash
git add core/llm_client.py core/brain.py tests/test_run_iteration.py tests/test_llm_client.py
git commit -m "feat(cache): supports_cache_control gate + _cache_boundary tag on last memory msg (T1.1)"
```

---

## Task 4: Local/DashScope marking — two breakpoints

**Files:**
- Modify: `core/llm_client.py` (replace `_mark_system_cache` at lines 190–211; update call in `_local_complete` at line 175)
- Test: `tests/test_llm_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm_client.py`:

```python
def _ephemeral(block):
    return block.get("cache_control") == {"type": "ephemeral"}


def test_mark_cache_breakpoints_marks_system_and_boundary():
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "tool_call_id": "1", "content": "{}", "_cache_boundary": True},
        {"role": "user", "content": "frame"},  # volatile tail, must stay untouched
    ]
    out = LLMClient._mark_cache_breakpoints(msgs)
    # system marked
    assert _ephemeral(out[0]["content"][-1])
    # boundary (tool) marked, tag popped
    assert _ephemeral(out[2]["content"][-1])
    assert "_cache_boundary" not in out[2]
    # tail untouched (still a plain string, no marker)
    assert out[3]["content"] == "frame"


def test_mark_cache_breakpoints_system_only_when_no_boundary():
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
    out = LLMClient._mark_cache_breakpoints(msgs)
    assert _ephemeral(out[0]["content"][-1])
    assert out[1]["content"] == "hi"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_llm_client.py -k mark_cache_breakpoints -v`
Expected: FAIL — `AttributeError: ... _mark_cache_breakpoints`.

- [ ] **Step 3: Replace `_mark_system_cache` with `_wrap_last_block` + `_mark_cache_breakpoints`**

In `core/llm_client.py`, replace the entire `_mark_system_cache` static method (lines 190–211) with:

```python
    @staticmethod
    def _wrap_last_block(m: dict) -> dict:
        """Return a copy of message `m` with cache_control:ephemeral on its last content
        block. String content is wrapped into a single text block; an unexpected content
        shape is returned untouched. The 1024-token minimum is satisfied by the system
        prompt (system marker) and by system+memory (the moving end-of-memory marker)."""
        content = m.get("content")
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = [dict(b) for b in content]
        else:
            return m
        blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        return {**m, "content": blocks}

    @staticmethod
    def _mark_cache_breakpoints(messages: list[dict]) -> list[dict]:
        """Mark two ephemeral breakpoints (DashScope path): the system message (a floor
        that survives compaction) and the message tagged `_cache_boundary` (the moving
        end-of-memory breakpoint). Pops the tag. Returns a new list; input untouched."""
        out = [dict(m) for m in messages]
        for i, m in enumerate(out):
            if m.get("role") == "system":
                out[i] = LLMClient._wrap_last_block(m)
                break
        for i in range(len(out) - 1, -1, -1):
            if out[i].pop("_cache_boundary", False):
                out[i] = LLMClient._wrap_last_block(out[i])
                break
        return out
```

- [ ] **Step 4: Update the call in `_local_complete`**

In `core/llm_client.py` line 174–175, replace:

```python
        if self._cache_system:
            messages = self._mark_system_cache(messages)
```

with:

```python
        if self._cache_system:
            messages = self._mark_cache_breakpoints(messages)
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: PASS (all `test_llm_client.py` tests).

- [ ] **Step 6: Commit**

```bash
git add core/llm_client.py tests/test_llm_client.py
git commit -m "feat(cache): two DashScope breakpoints — system floor + moving end-of-memory (T1.1)"
```

---

## Task 5: Claude path caching + param threading

**Files:**
- Modify: `core/llm_client.py` (`chat_complete` line 113; `_claude_complete` lines 250–284; `_consolidate_tool_results` lines 286–307)
- Test: `tests/test_llm_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm_client.py`:

```python
def test_consolidate_tool_results_marks_boundary_block():
    msgs = [
        {"role": "tool", "tool_call_id": "1", "content": "{}", "_cache_boundary": True},
    ]
    out = LLMClient._consolidate_tool_results(msgs)
    assert out[0]["role"] == "user"
    assert out[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "_cache_boundary" not in out[0]


def test_consolidate_tool_results_unmarked_when_no_boundary():
    msgs = [{"role": "tool", "tool_call_id": "1", "content": "{}"}]
    out = LLMClient._consolidate_tool_results(msgs)
    assert "cache_control" not in out[0]["content"][-1]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_llm_client.py -k consolidate -v`
Expected: FAIL — `KeyError: 'cache_control'` (the marker isn't applied yet).

- [ ] **Step 3: Mark the boundary block in `_consolidate_tool_results`**

In `core/llm_client.py`, replace the `_consolidate_tool_results` method (lines 286–307) with:

```python
    @staticmethod
    def _consolidate_tool_results(messages: list[dict]) -> list[dict]:
        """Merge consecutive tool-result messages into single user messages. A message
        tagged `_cache_boundary` gets cache_control:ephemeral on its last block (Anthropic
        end-of-memory breakpoint); the tag is dropped."""
        out: list[dict] = []
        i = 0
        while i < len(messages):
            m = messages[i]
            if m.get("role") == "tool":
                block: list[dict] = []
                boundary = False
                while i < len(messages) and messages[i].get("role") == "tool":
                    tr = messages[i]
                    boundary = boundary or tr.get("_cache_boundary", False)
                    block.append({
                        "type": "tool_result",
                        "tool_use_id": tr["tool_call_id"],
                        "content": tr["content"],
                    })
                    i += 1
                if boundary:
                    block[-1] = {**block[-1], "cache_control": {"type": "ephemeral"}}
                out.append({"role": "user", "content": block})
            else:
                if m.pop("_cache_boundary", False):
                    m = LLMClient._mark_block_in_message(m)
                out.append(m)
                i += 1
        return out

    @staticmethod
    def _mark_block_in_message(m: dict) -> dict:
        """Add cache_control:ephemeral to the last content block of a non-tool message.
        Anthropic assistant/user content is a list of blocks; a bare string is wrapped."""
        content = m.get("content")
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = [dict(b) for b in content]
        else:
            return {k: v for k, v in m.items() if k != "_cache_boundary"}
        blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        return {**{k: v for k, v in m.items() if k != "_cache_boundary"}, "content": blocks}
```

Note: the `_cache_boundary` tag is popped from the dict copy inside the `tool` branch via the `boundary` flag (originals are not mutated there); in the non-tool branch `m.pop` mutates the passed dict, which is the freshly built `build_loop_messages` copy — safe.

- [ ] **Step 4: Thread `thinking`/`max_tokens` and mark `system` in `_claude_complete`**

In `core/llm_client.py` line 113, replace:

```python
        return await self._claude_complete(messages, tools)
```

with:

```python
        return await self._claude_complete(
            messages, tools, thinking=thinking, max_tokens=max_tokens,
        )
```

Then replace the `_claude_complete` signature and the `system`/`kwargs` section (lines 250–283). Replace:

```python
    async def _claude_complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        # Separate system message
        system = ""
        non_system: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                non_system.append(m)
```

with:

```python
    async def _claude_complete(
        self,
        messages: list[dict],
        tools: list[dict],
        thinking: bool = False,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        # Separate system message
        system = ""
        non_system: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                non_system.append(m)
```

And replace the `kwargs`/`system` block (lines 273–281):

```python
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": consolidated,
        }
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
```

with:

```python
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens or 4096,
            "messages": consolidated,
        }
        if system:
            # System floor breakpoint (Anthropic honors up to 4 cache breakpoints).
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
        # TODO: Anthropic extended thinking — `thinking` accepted but not yet wired.
        _ = thinking
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: PASS (all tests). Then confirm import: `python -c "import core.llm_client"` (exit 0).

- [ ] **Step 6: Commit**

```bash
git add core/llm_client.py tests/test_llm_client.py
git commit -m "feat(cache): Claude path caching (system + end-of-memory) + thread max_tokens (T2.1)"
```

---

## Task 6: Update `sim_loop.py` to the new constants

**Files:**
- Modify: `scripts/sim_loop.py` (lines ~121, ~142 reference `brain.COMPACT_AT` / `brain.COMPACT_KEEP`)

- [ ] **Step 1: Inspect the two references**

Run: `grep -n "COMPACT_AT\|COMPACT_KEEP" scripts/sim_loop.py`
Expected: two lines (~121 print header, ~142 assertion comment).

- [ ] **Step 2: Update line ~121**

Replace:

```python
          f"COMPACT_AT={brain.COMPACT_AT} KEEP={brain.COMPACT_KEEP} FLOOR={brain.LOOP_FLOOR}s")
```

with:

```python
          f"COMPACT_AT_TOKENS={brain.COMPACT_AT_TOKENS} KEEP_TOKENS={brain.COMPACT_KEEP_TOKENS} FLOOR={brain.LOOP_FLOOR}s")
```

- [ ] **Step 3: Update line ~142**

This line asserts the trimmed turn count against `brain.COMPACT_AT` (a turn count), which no longer exists. Replace the assertion that references `brain.COMPACT_AT` so it checks memory tokens instead. Replace:

```python
          f"(should be ≤ {brain.COMPACT_AT})")
```

with:

```python
          f"(memory est. tokens should settle ≤ {brain.COMPACT_KEEP_TOKENS} after a trim)")
```

If the surrounding line computes a turn count for that message, switch it to `brain.estimate_memory_tokens(brain.memory)` (import already available via `brain`). Inspect the full statement first and keep the print's intent (report post-compaction memory size).

- [ ] **Step 4: Smoke-run sim_loop offline**

Run: `python -m scripts.sim_loop --help 2>/dev/null || python -c "import scripts.sim_loop"`
Expected: imports without `AttributeError` on the removed constants.

- [ ] **Step 5: Commit**

```bash
git add scripts/sim_loop.py
git commit -m "chore(sim): sim_loop uses token compaction constants (T2.3)"
```

---

## Task 7: O-1 probe harness — `--moving` mode (NOT run)

**Files:**
- Modify: `scripts/test_cache.py` (add `--moving` arg + a growing-memory loop)

This task only *adds* the cloud probe. **Do not run it.** Running it against DashScope spends real tokens and requires explicit per-instance user approval (`CLAUDE.md`). The implementer commits the harness and stops.

- [ ] **Step 1: Add `--moving` to the arg parser**

In `scripts/test_cache.py`, after the `-n` argument (line 34), add:

```python
    ap.add_argument("--moving", action="store_true",
                    help="Simulate the loop: grow memory each call, mark system + "
                         "end-of-memory breakpoint, report cached_tokens per call (O-1).")
```

- [ ] **Step 2: Add the moving-breakpoint probe branch**

In `scripts/test_cache.py`, replace the existing call loop (lines 50–61, the `for i in range(args.n):` block) with:

```python
    if args.moving:
        from core.brain import build_loop_messages
        from core.scratchpad import Scratchpad
        memory: list[dict] = []
        for i in range(args.n):
            # append one synthetic prior turn (assistant + tool result) each call
            memory.append({"role": "assistant", "content": f"observation {i}: nothing new"})
            memory.append({"role": "tool", "tool_call_id": str(i),
                           "content": '{"ok": true, "result": {"distance_cm": -1}}'})
            messages = build_loop_messages(SYSTEM_PROMPT, memory, [], Scratchpad(),
                                           cache_boundary=llm.supports_cache_control)
            r = await llm.chat_complete(messages, TOOL_SCHEMAS, max_tokens=16)
            u = r.usage or {}
            p, cached = u.get("prompt_tokens", 0), u.get("cached_tokens", 0)
            pct = f"{cached / p * 100:.0f}%" if p else "—"
            flag = "  <-- CACHE HIT" if cached else ""
            print(f"  call {i}: prompt={p}  cached={cached} ({pct})  mem_msgs={len(memory)}{flag}")
        print("-" * 64)
        print("O-1: cached should GROW with the prefix (proves the moving end-of-memory")
        print("breakpoint, not just a fixed system block). If cached plateaus at the")
        print("system-only size, DashScope is honoring 1 breakpoint — flip to 1 marker.")
        print("If call 0 errors on the tool-role block, apply the §B assistant/user fallback.")
        await llm.close()
        return

    # Same stable prefix every call (system prompt + tools), tiny varying user turn.
    for i in range(args.n):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"reply with one word (call {i})"},
        ]
        r = await llm.chat_complete(messages, TOOL_SCHEMAS, max_tokens=16)
        u = r.usage or {}
        p = u.get("prompt_tokens", 0)
        cached = u.get("cached_tokens", 0)
        pct = f"{cached / p * 100:.0f}%" if p else "—"
        flag = "  <-- CACHE HIT" if cached else ""
        print(f"  call {i}: prompt={p}  cached={cached} ({pct}){flag}")
```

- [ ] **Step 3: Verify the script imports (no cloud call)**

Run: `python -c "import scripts.test_cache"`
Expected: exit 0, no network.

- [ ] **Step 4: Commit**

```bash
git add scripts/test_cache.py
git commit -m "test(cache): add --moving O-1 probe for the end-of-memory breakpoint (not run)"
```

- [ ] **Step 5: STOP and propose the probe to the user**

Do not run any DashScope command. Surface the proposed command and wait for explicit approval:

```
python -m scripts.test_cache --dashscope qwen3.5-flash --moving -n 4
```

---

## Final verification

- [ ] **Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: all green, including the rewritten compaction tests and the new `tests/test_llm_client.py`.

- [ ] **Import smoke-check**

Run: `python -c "import core.brain, core.llm_client, core.loop_helpers, scripts.sim_loop, scripts.test_cache"`
Expected: exit 0.

---

## Self-review notes (author)

- **Spec §A** → Task 3 (`supports_cache_control`, `cache_boundary` plumbing).
- **Spec §B** → Task 4 (`_wrap_last_block`, `_mark_cache_breakpoints`, two markers); tool-role risk surfaced in Task 7 probe output + §B fallback.
- **Spec §C** → Task 5 (Claude system list + boundary block + `max_tokens`; `thinking` TODO).
- **Spec §D** → Tasks 1–2 (`estimate_memory_tokens`, `maybe_compact`, constants, call site).
- **Spec §E** → Tasks 1, 3, 4, 5, 6 (tests + sim_loop).
- **Spec §F** → Task 7 (probe, not run).
- **Naming consistency:** `_cache_boundary` (tag), `supports_cache_control` (property), `_mark_cache_breakpoints` / `_wrap_last_block` (local), `_consolidate_tool_results` / `_mark_block_in_message` (Claude), `estimate_memory_tokens` / `maybe_compact(at_tokens, keep_tokens)`, `COMPACT_AT_TOKENS` / `COMPACT_KEEP_TOKENS` — used identically across tasks.
