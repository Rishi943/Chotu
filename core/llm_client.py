"""LLM client abstraction — swap providers via PALIV_LLM_PROVIDER=local|claude."""

import json
import os
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False


# ---------------------------------------------------------------------------
# Normalised response types
# brain.py uses these regardless of provider.
# ---------------------------------------------------------------------------

@dataclass
class ToolFunction:
    name: str
    arguments: str  # JSON string — matches OpenAI's tc.function.arguments


@dataclass
class ToolCall:
    id: str
    function: ToolFunction


@dataclass
class NormalizedMessage:
    content: Optional[str]
    tool_calls: Optional[list[ToolCall]]


@dataclass
class NormalizedChoice:
    message: NormalizedMessage


@dataclass
class LLMResponse:
    choices: list[NormalizedChoice]
    usage: Optional[dict] = None  # {"prompt_tokens": N, "completion_tokens": N, "timings": {...}}


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Provider-agnostic LLM wrapper.

    PALIV_LLM_PROVIDER=local  (default) — llama-server via AsyncOpenAI
    PALIV_LLM_PROVIDER=claude           — Anthropic API (requires anthropic package + ANTHROPIC_API_KEY)
    """

    def __init__(self):
        self.provider = os.getenv("PALIV_LLM_PROVIDER", "local")

        if self.provider == "local":
            base_url = os.getenv("PALIV_BRAIN_URL", "http://localhost:8080/v1")
            api_key = os.getenv("PALIV_BRAIN_KEY", "not-needed")
            self.model = os.getenv("PALIV_BRAIN_MODEL", "Qwen3.5-4B-Q4_K_M.gguf")
            self._openai = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=120.0)
            # Explicit context cache (cache_control ephemeral markers) — DashScope only.
            # llama.cpp doesn't understand the marker, so gate on the endpoint.
            self._cache_system = "dashscope" in base_url.lower()
            self._anthropic_client = None

        elif self.provider == "claude":
            if not _ANTHROPIC_AVAILABLE:
                raise ImportError(
                    "anthropic package required for claude provider: pip install anthropic"
                )
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not set")
            self.model = os.getenv("PALIV_BRAIN_MODEL", "claude-sonnet-4-6")
            self._anthropic_client = _anthropic.AsyncAnthropic(api_key=api_key)
            self._openai = None
            self._cache_system = False

        else:
            raise ValueError(
                f"Unknown PALIV_LLM_PROVIDER: {self.provider!r}. Use 'local' or 'claude'."
            )

    async def close(self) -> None:
        if self._openai:
            await self._openai.close()
        if self._anthropic_client:
            await self._anthropic_client.close()

    async def chat_complete(
        self,
        messages: list[dict],
        tools: list[dict],
        thinking: bool = False,
        tool_choice: Optional[dict] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """Send messages + tools to the configured provider. Returns normalised response."""
        if self.provider == "local":
            return await self._local_complete(
                messages, tools, thinking=thinking,
                tool_choice=tool_choice, max_tokens=max_tokens,
            )
        return await self._claude_complete(messages, tools)

    @property
    def supports_cache_control(self) -> bool:
        """True when the active provider honors cache_control markers: Claude always,
        local only when pointed at DashScope. Gates the _cache_boundary tag so it never
        reaches llama-server (which would reject the unknown field)."""
        return self.provider == "claude" or self._cache_system

    def format_assistant_message(self, response: LLMResponse) -> dict:
        """
        Return the assistant-turn dict to append to the messages list for the next call.
        Format is provider-specific.
        """
        msg = response.choices[0].message
        if self.provider == "local":
            d: dict = {"role": "assistant"}
            if msg.content is not None:
                d["content"] = msg.content
            if msg.tool_calls:
                d["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            return d
        else:
            # Anthropic format
            content_blocks: list[dict] = []
            if msg.content:
                content_blocks.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls or []:
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": json.loads(tc.function.arguments) if tc.function.arguments else {},
                })
            return {"role": "assistant", "content": content_blocks}

    def format_tool_result(self, tool_call_id: str, content: str) -> dict:
        """Return a tool-result dict to append to the messages list."""
        if self.provider == "local":
            return {"role": "tool", "tool_call_id": tool_call_id, "content": content}
        # Anthropic: tool results live in a user message
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_call_id, "content": content}],
        }

    # -----------------------------------------------------------------------
    # Local (llama-server) backend
    # -----------------------------------------------------------------------

    async def _local_complete(
        self,
        messages: list[dict],
        tools: list[dict],
        thinking: bool = False,
        tool_choice: Optional[dict] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        if self._cache_system:
            messages = self._mark_cache_breakpoints(messages)
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": thinking}},
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        raw = await self._openai.chat.completions.create(**kwargs)
        return self._normalise_openai(raw)

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

    @staticmethod
    def _normalise_openai(raw) -> LLMResponse:
        choices = []
        for c in raw.choices:
            m = c.message
            tcs = None
            if m.tool_calls:
                tcs = [
                    ToolCall(id=tc.id, function=ToolFunction(
                        name=tc.function.name,
                        arguments=tc.function.arguments or "{}",
                    ))
                    for tc in m.tool_calls
                ]
            choices.append(NormalizedChoice(message=NormalizedMessage(
                content=m.content, tool_calls=tcs,
            )))
        usage = None
        if raw.usage:
            usage = {
                "prompt_tokens": raw.usage.prompt_tokens,
                "completion_tokens": raw.usage.completion_tokens,
            }
            # DashScope/OpenAI: implicit-cache hits reported here, included in prompt_tokens.
            details = getattr(raw.usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", None) if details else None
            if cached is not None:
                usage["cached_tokens"] = cached
            timings = (raw.model_extra or {}).get("timings", {})
            if timings:
                usage["timings"] = timings
        return LLMResponse(choices=choices, usage=usage)

    # -----------------------------------------------------------------------
    # Claude (Anthropic) backend
    # -----------------------------------------------------------------------

    async def _claude_complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        # Separate system message
        system = ""
        non_system: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                non_system.append(m)

        # Consolidate consecutive tool-result messages (Anthropic requires them in one user msg)
        consolidated = self._consolidate_tool_results(non_system)

        # Translate tool schemas: OpenAI → Anthropic
        anthropic_tools = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
            }
            for t in (tools or [])
        ]

        kwargs: dict = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": consolidated,
        }
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        raw = await self._anthropic_client.messages.create(**kwargs)
        return self._normalise_anthropic(raw)

    @staticmethod
    def _consolidate_tool_results(messages: list[dict]) -> list[dict]:
        """Merge consecutive tool-result messages into single user messages."""
        out: list[dict] = []
        i = 0
        while i < len(messages):
            m = messages[i]
            if m.get("role") == "tool":
                block: list[dict] = []
                while i < len(messages) and messages[i].get("role") == "tool":
                    tr = messages[i]
                    block.append({
                        "type": "tool_result",
                        "tool_use_id": tr["tool_call_id"],
                        "content": tr["content"],
                    })
                    i += 1
                out.append({"role": "user", "content": block})
            else:
                out.append(m)
                i += 1
        return out

    @staticmethod
    def _normalise_anthropic(raw) -> LLMResponse:
        content_text: Optional[str] = None
        tool_calls: list[ToolCall] = []
        for block in raw.content:
            if block.type == "text":
                content_text = block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    function=ToolFunction(
                        name=block.name,
                        arguments=json.dumps(block.input),
                    ),
                ))
        usage = None
        if hasattr(raw, "usage") and raw.usage:
            usage = {
                "prompt_tokens": raw.usage.input_tokens,
                "completion_tokens": raw.usage.output_tokens,
            }
        return LLMResponse(choices=[NormalizedChoice(message=NormalizedMessage(
            content=content_text,
            tool_calls=tool_calls or None,
        ))], usage=usage)
