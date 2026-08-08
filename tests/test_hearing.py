"""Parsing the model's reply into text + source-language name. No network."""
from core.hearing import parse_hearing


def test_translation_is_split_off_after_the_marker():
    out = parse_hearing("show how to be a fish\nEnglish: walk right")
    assert out["text"] == "walk right"
    assert out["language"] == "Marathi"


def test_missing_marker_is_not_fatal():
    out = parse_hearing("walk forward two steps")
    assert out["text"] == "walk forward two steps"
    assert out["language"] == "Marathi"


def test_whitespace_is_stripped():
    out = parse_hearing('  Marathi: one line\nEnglish:   do three push ups  ')
    assert out["text"] == "do three push ups"
    assert out["language"] == "Marathi"


def test_empty_reply_gives_empty_text_not_a_crash():
    out = parse_hearing("")
    assert out["text"] == "" and out["language"] == "Marathi"

