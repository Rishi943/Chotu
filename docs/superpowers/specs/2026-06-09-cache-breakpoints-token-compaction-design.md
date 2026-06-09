# Cache Breakpoints + Token-Pressure Compaction — Design

**Date:** 2026-06-09
**Source:** `docs/pi_audit.md` findings T1.1, T2.1, T2.3.
**Branch target:** `loop-token-efficiency` (mergeable now).
**Scope:** move the DashScope cache breakpoint to the end of the stable `memory` prefix
(plus a system floor), port caching + param threading to the Claude path, and switch
compaction from a turn-count trigger to a memory-token trigger with token-aware trimming.

All code and tests are local-verifiable (free). The only cloud touch is the O-1 probe
(§F), which is **not** run without explicit per-instance approval (`CLAUDE.md` rule,
`feedback_cloud_token_approval`).

---

## Decisions (locked with user)

- **T1.1 markers:** two — `system` (compaction-surviving floor) + end-of-`memory`
  (moving breakpoint). Audit risk note: worst case DashScope ignores the 2nd marker =
  no regression. O-1 probe confirms/tunes.
- **T2.3 trim:** token-aware on both trigger *and* keep (drop whole oldest turns until
  the remainder is under the keep target), not turn-count.
- **T2.1 Claude scope:** add cache breakpoints + thread `max_tokens`; accept `thinking`
  but leave Anthropic extended-thinking unwired (TODO). Claude is fallback-only.

---

## A. Boundary plumbing (provider-agnostic)

The boundary is *identified* once in `brain.py` and *marked* per provider.

`build_loop_messages(system_prompt, memory, frame_stack, scratchpad, cache_boundary=False)`:

```python
msgs = [{"role": "system", "content": system_prompt}]
mem_msgs = strip_internal_fields(memory)
if cache_boundary and mem_msgs:
    mem_msgs[-1] = {**mem_msgs[-1], "_cache_boundary": True}
msgs.extend(mem_msgs)
# ... STATE block, then frames (volatile tail, unchanged) ...
```

Call site (`run_iteration`):

```python
messages = build_loop_messages(
    SYSTEM_PROMPT, memory, frame_stack, scratchpad,
    cache_boundary=llm_client.supports_cache_control,
)
```

New property on `LLMClient`:

```python
@property
def supports_cache_control(self) -> bool:
    return self.provider == "claude" or self._cache_system
```

`_cache_system` keeps its current meaning (DashScope-in-URL gate, set in `__init__`;
name retained because `scripts/test_cache.py` reads it). This single gate guarantees the
internal `_cache_boundary` tag is **only** added when a provider can consume it — so it
never leaks to llama-server, which would reject the unknown field.

**Why end-of-memory works on this layout.** Prefix `[system │ memory]` is append-only
between compactions. Tick N marks the last memory msg → cloud stores `hash(system+m1..mk)`.
Tick N+1's memory is that plus the new turn's appends; we mark the new last msg. The API
auto-reads the longest cached prefix (`system+m1..mk`) and only *writes* the ~1 new turn.
Ephemeral TTL (~5 min) >> heartbeat gap, so the entry survives tick-to-tick. The volatile
tail (STATE + 3 frames) sits *after* the boundary, so it never invalidates the cache.

**Why also mark system.** At compaction `m1..m30 → m23..m30`, the end-of-memory cache
entry dies (prefix content changed). A surviving `system` marker keeps that floor warm
across the rare compaction; without it every post-compaction tick is a fully cold write.

---

## B. Local / DashScope marking (T1.1)

Rename `_mark_system_cache → _mark_cache_breakpoints`; extract the block-wrap helper.

```python
@staticmethod
def _wrap_last_block(m: dict) -> dict:
    content = m.get("content")
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        blocks = [dict(b) for b in content]
    else:
        return m  # unexpected shape — leave untouched
    blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    return {**m, "content": blocks}

@staticmethod
def _mark_cache_breakpoints(messages: list[dict]) -> list[dict]:
    out = [dict(m) for m in messages]
    for i, m in enumerate(out):              # system floor
        if m.get("role") == "system":
            out[i] = LLMClient._wrap_last_block(m)
            break
    for i in range(len(out) - 1, -1, -1):    # moving breakpoint
        if out[i].pop("_cache_boundary", False):
            out[i] = LLMClient._wrap_last_block(out[i])
            break
    return out
```

`_local_complete` calls it only when `self._cache_system` (unchanged gate).

**⚠ Known risk — tool-role boundary.** The end-of-memory message is *usually a `tool`
result* (memory ends with the prior tick's tool outputs). Wrapping a tool message's
string content into a `[{type:text,...}]` block with `cache_control` is valid for
Anthropic but **unverified for DashScope's OpenAI-compat tool role**. The O-1 probe (§F)
must confirm it caches rather than errors. **Fallback** if rejected: scan for the last
`assistant`/`user` message to mark instead, ceding the small (≤1500-char-capped)
tool-result tail from the cached prefix.

---

## C. Claude marking + param threading (T2.1)

`chat_complete` forwards `thinking`/`max_tokens` into `_claude_complete(messages, tools,
thinking, max_tokens)`.

In `_claude_complete`:
- `system` becomes a one-block list with `cache_control`:
  `[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]`.
- During `_consolidate_tool_results`, the message carrying `_cache_boundary` gets
  `cache_control` on its last content block (tool_result blocks accept it natively);
  pop the tag.
- `max_tokens` threaded through, default `4096` (preserves current behavior when unset).
- `thinking` accepted; unwired with `# TODO: Anthropic extended thinking`.

Anthropic honors 4 breakpoints, so system + memory is safe with no O-1 dependency.

---

## D. Token-aware compaction (T2.3)

New pure helper in `loop_helpers.py`:

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
```

Rewrite `maybe_compact`:

```python
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
    if starts:                       # even the last turn exceeds keep — keep it alone
        del memory[:starts[-1]]
```

Constants in `brain.py` (replace `COMPACT_AT`/`COMPACT_KEEP`):

```python
COMPACT_AT_TOKENS   = int(os.getenv("PALIV_COMPACT_AT_TOKENS", "10000"))
COMPACT_KEEP_TOKENS = int(os.getenv("PALIV_COMPACT_KEEP_TOKENS", "6000"))
```

Sized for 32k Qwen per audit (keep ≈ 6k); both env-tunable. Call site:
`maybe_compact(memory, COMPACT_AT_TOKENS, COMPACT_KEEP_TOKENS)`.

---

## E. Tests + harness updates

- `tests/test_loop_helpers.py`: rewrite the 3 `maybe_compact` tests to token-based
  (build memory with known char sizes; assert no-op below `at_tokens`, trims to
  `≤ keep_tokens`, preserves turn alignment / no orphan tool results). Add
  `estimate_memory_tokens` tests (string content, tool_calls args, list content).
- `tests/test_run_iteration.py`: add a `cache_boundary=True` test (last memory msg
  tagged `_cache_boundary`; existing calls unaffected — param defaults False).
- New `llm_client` tests: `_mark_cache_breakpoints` marks system + boundary and pops
  the tag; `_wrap_last_block` handles str and list content; `supports_cache_control`
  matrix (local+dashscope=True, local+llama=False, claude=True).
- `scripts/sim_loop.py`: update `brain.COMPACT_AT` / `brain.COMPACT_KEEP` references
  (lines ~121, ~142) to the new token constants.

All runnable against local llama-server / pure unit tests — no cloud.

---

## F. O-1 probe (cloud — explicit approval required, not run otherwise)

Extend `scripts/test_cache.py` with a `--moving` mode simulating the loop: each call
appends a synthetic prior turn to memory, marks system + end-of-memory, and prints
`prompt`/`cached` per call.

Proposed command (await approval):

```
python -m scripts.test_cache --dashscope qwen3.5-flash --moving -n 4
```

Answers O-1:
1. Does DashScope honor the 2nd breakpoint (does `cached` track the *growing* prefix,
   not just the fixed system block)?
2. Is there a write premium on the turn a new prefix block is first stored?
3. Does it accept `cache_control` on a tool-role block (the §B risk)?

Result tunes 1-vs-2 markers and confirms the tool-role wrapping; on rejection, apply the
§B fallback.

---

## Sequencing

1. B + C + D + all of E land first (local-verifiable, no regression risk).
2. Propose the §F probe command; wait for approval.
3. Probe result tunes markers / confirms tool-role wrapping; apply §B fallback if needed.

## Non-goals

- T2.2 (image-downgrade guard), T2.4 (cache-read/write meter split), T3.x — out of scope
  for this spec.
- Anthropic extended-thinking wiring (TODO placeholder only).
- Any change to the local llama KV-cache behavior (already optimal via the stable prefix).
