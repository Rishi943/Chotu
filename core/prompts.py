"""System prompt loader. Composes PALIV.md (framework contract) +
CHOTU_BASE.md (persona + heartbeat rhythm) into a single stateless prompt."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_system_prompt() -> str:
    """Compose PALIV.md + CHOTU_BASE.md into the runtime system prompt."""
    paliv = (REPO_ROOT / "PALIV.md").read_text(encoding="utf-8")
    base = (REPO_ROOT / "CHOTU_BASE.md").read_text(encoding="utf-8")
    return f"{paliv}\n\n{base}"


SYSTEM_PROMPT = load_system_prompt()
