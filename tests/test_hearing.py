"""Parsing the model's reply into text + language. No network."""
import pytest
from core.hearing import parse_hearing


def test_plain_english_reply_is_the_text():
    out = parse_hearing("Language: Marathi\nshow how to be a fish")
    assert out["text"] == "show how to be a fish"
    assert out["language"] == "Marathi"


def test_missing_language_line_is_not_fatal():
    out = parse_hearing("walk forward two steps")
    assert out["text"] == "walk forward two steps"
    assert out["language"] == ""


def test_whitespace_and_quotes_are_stripped():
    out = parse_hearing('Language: Hindi\n  "do three push ups"  ')
    assert out["text"] == "do three push ups"


def test_empty_reply_gives_empty_text_not_a_crash():
    out = parse_hearing("")
    assert out["text"] == "" and out["language"] == ""
