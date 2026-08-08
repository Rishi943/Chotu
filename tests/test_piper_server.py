"""Tests for the resident Piper service and its client (design 2026-08-08).

Covers the pure helpers only -- reply cap, the CLI-args -> SynthesisConfig
mapping (including that a client-supplied ``-s`` can never override the reel
speaker), and the WAV<->PCM plumbing -- so the 78 MB model is never loaded in a
unit test.
"""

import core.piper_server as ps
from core import tools


def test_cap_reply_truncates_long_text():
    capped = tools._cap_reply("x" * 500)
    assert len(capped) == tools._SPEAK_REPLY_CAP


def test_cap_reply_leaves_short_text_alone():
    assert tools._cap_reply("hello") == "hello"


def test_parse_piper_args_sets_pacing_and_keeps_reel_speaker():
    # A client may forward a -s flag; the reel speaker 668 must win anyway.
    cfg = ps._parse_piper_args(
        ["--length-scale", "1.0", "--volume", "2.0",
         "--sentence-silence", "0.6", "-s", "123"],
        speaker=668,
    )
    assert cfg.speaker_id == 668
    assert cfg.length_scale == 1.0
    assert cfg.volume == 2.0


def test_wav_bytes_writes_valid_riff():
    wav = ps._wav_bytes(b"\x00\x00" * 10, 22050)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[36:40] == b"data"
    assert len(wav) == 44 + 20


def test_wav_to_pcm_recovers_pcm():
    pcm = b"\x0a\x0b" * 5
    wav = ps._wav_bytes(pcm, 22050)
    assert tools._wav_to_pcm(wav) == pcm