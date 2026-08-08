"""The one always-loaded file. Everything else is reached with `read`."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHOTU_MD_PATH = ROOT / "CHOTU.md"
DOCS_DIR = ROOT / "docs"


def load_system_prompt() -> str:
    return CHOTU_MD_PATH.read_text(encoding="utf8").strip()


SYSTEM_PROMPT = load_system_prompt()
