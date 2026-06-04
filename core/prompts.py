"""System prompt loader. Composes PALIV.md (framework) + CHOTU_BASE.md
(persona) + a mode-specific overlay (CHOTU_STATELESS.md or CHOTU_LIVE.md)
selected by PALIV_BRAIN_MODE env (default: stateless)."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_system_prompt(mode: str | None = None) -> str:
    """Compose PALIV.md + CHOTU_BASE.md + CHOTU_{MODE}.md.

    mode: "stateless" (default) or "live". If None, reads PALIV_BRAIN_MODE env.
    """
    mode = (mode or os.getenv("PALIV_BRAIN_MODE", "stateless")).strip().lower()
    if mode not in ("stateless", "live"):
        raise ValueError(f"PALIV_BRAIN_MODE must be 'stateless' or 'live', got {mode!r}")

    paliv = (REPO_ROOT / "PALIV.md").read_text(encoding="utf-8")
    base = (REPO_ROOT / "CHOTU_BASE.md").read_text(encoding="utf-8")
    overlay_name = "CHOTU_STATELESS.md" if mode == "stateless" else "CHOTU_LIVE.md"
    overlay = (REPO_ROOT / overlay_name).read_text(encoding="utf-8")

    return f"{paliv}\n\n{base}\n\n{overlay}"


SYSTEM_PROMPT = load_system_prompt()
