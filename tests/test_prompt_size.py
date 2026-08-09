"""The always-loaded context is one page, and it is one file."""
import pathlib
import core.prompts as prompts

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_only_one_markdown_file_at_root():
    md = sorted(p.name for p in ROOT.glob("*.md"))
    assert md == ["CHOTU.md"], f"expected only CHOTU.md at root, found {md}"


def test_chotu_md_is_under_one_page():
    """Raised from 500 to 800 words on 2026-08-09, when the file took on the
    answer format and the worked examples.

    The examples are not padding -- they are what the model actually learns
    from. Measured: prose RULES about how to reply cost tool accuracy (four
    stacked rules scored 0/8), while EXAMPLES cost nothing and fixed things
    rules could not. Multi-step commands went 0/5 to 5/5 on one added example.
    So the ceiling exists to stop rules piling up, not examples; if this fails,
    check which kind of text grew before raising it again."""
    text = (ROOT / "CHOTU.md").read_text(encoding="utf8")
    assert len(text.split()) < 800, "CHOTU.md must stay near a page"


def test_system_prompt_comes_from_chotu_md():
    assert "Chotu" in prompts.SYSTEM_PROMPT
    assert len(prompts.SYSTEM_PROMPT.split()) < 800
