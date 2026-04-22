"""LLM client abstraction — swap providers via CHOTU_LLM_PROVIDER=local|claude."""

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


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Provider-agnostic LLM wrapper.

    CHOTU_LLM_PROVIDER=local  (default) — llama-server via AsyncOpenAI
    CHOTU_LLM_PROVIDER=claude           — Anthropic API (requires anthropic package + ANTHROPIC_API_KEY)
    """

    def __init__(self):
        self.provider = os.getenv("CHOTU_LLM_PROVIDER", "local")

        if self.provider == "local":
            base_url = os.getenv("CHOTU_BRAIN_URL", "http://localhost:8080/v1")
            api_key = os.getenv("CHOTU_BRAIN_KEY", "not-needed")
            self.model = os.getenv("CHOTU_BRAIN_MODEL", "Qwen3.5-4B-Q4_K_M.gguf")
            self._openai = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=120.0)
            self._anthropic_client = None

        elif self.provider == "claude":
            if not _ANTHROPIC_AVAILABLE:
                raise ImportError(
                    "anthropic package required for claude provider: pip install anthropic"
                )
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not set")
            self.model = os.getenv("CHOTU_BRAIN_MODEL", "claude-sonnet-4-6")
            self._anthropic_client = _anthropic.AsyncAnthropic(api_key=api_key)
            self._openai = None

        else:
            raise ValueError(
                f"Unknown CHOTU_LLM_PROVIDER: {self.provider!r}. Use 'local' or 'claude'."
            )

    async def chat_complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        """Send messages + tools to the configured provider. Returns normalised response."""
        if self.provider == "local":
            return await self._local_complete(messages, tools)
        return await self._claude_complete(messages, tools)

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

    async def _local_complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
        if tools:
            kwargs["tools"] = tools
        raw = await self._openai.chat.completions.create(**kwargs)
        return self._normalise_openai(raw)

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
        return LLMResponse(choices=choices)

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
        return LLMResponse(choices=[NormalizedChoice(message=NormalizedMessage(
            content=content_text,
            tool_calls=tool_calls or None,
        ))])
