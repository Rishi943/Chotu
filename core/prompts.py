"""System prompt loader. Composes PALIV.md (framework contract) +
a persona file into a single stateless prompt.

Persona selection: PALIV_PERSONA=reel loads CHOTU_REEL.md (first-boot reel
persona); anything else (or unset) loads CHOTU_BASE.md (everyday Chotu)."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PERSONA_FILES = {
    "reel": "CHOTU_REEL.md",
}
_DEFAULT_PERSONA_FILE = "CHOTU_BASE.md"


def load_system_prompt() -> str:
    """Compose PALIV.md + the selected persona file into the runtime prompt."""
    paliv = (REPO_ROOT / "PALIV.md").read_text(encoding="utf-8")
    persona_file = _PERSONA_FILES.get(os.environ.get("PALIV_PERSONA", ""),
                                      _DEFAULT_PERSONA_FILE)
    persona = (REPO_ROOT / persona_file).read_text(encoding="utf-8")
    return f"{paliv}\n\n{persona}"


SYSTEM_PROMPT = load_system_prompt()
