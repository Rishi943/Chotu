"""System prompt loader. Reads PALIV.md (framework) + CHOTU.md (persona) from repo root."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_system_prompt() -> str:
    paliv = (REPO_ROOT / "PALIV.md").read_text(encoding="utf-8")
    chotu = (REPO_ROOT / "CHOTU.md").read_text(encoding="utf-8")
    return paliv + "\n\n" + chotu


SYSTEM_PROMPT = load_system_prompt()
