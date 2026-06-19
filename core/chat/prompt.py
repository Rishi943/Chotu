"""System prompt loader for chat mode. Reads CHAT.md from repo root."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_chat_prompt() -> str:
    return (REPO_ROOT / "CHAT.md").read_text(encoding="utf-8")


CHAT_PROMPT = load_chat_prompt()
