"""The always-loaded context is one page, and it is one file."""
import pathlib
import core.prompts as prompts

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_only_one_markdown_file_at_root():
    md = sorted(p.name for p in ROOT.glob("*.md"))
    assert md == ["CHOTU.md"], f"expected only CHOTU.md at root, found {md}"


def test_chotu_md_is_under_one_page():
    text = (ROOT / "CHOTU.md").read_text(encoding="utf8")
    assert len(text.split()) < 500, "CHOTU.md must stay under a page"


def test_system_prompt_comes_from_chotu_md():
    assert "Chotu" in prompts.SYSTEM_PROMPT
    assert len(prompts.SYSTEM_PROMPT.split()) < 600
