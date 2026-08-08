"""Parsing the model's reply into text + source-language name. No network."""
import io
import struct
import wave

from core.hearing import parse_hearing, pcm_to_wav


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


def test_pcm_to_wav_wraps_float32_pcm_into_a_valid_wav():
    # One second of 16 kHz mono digital silence, as a little-endian float32
    # buffer — exactly the shape the console posts to /audio.
    frames = 16000
    pcm = b"".join(struct.pack("<f", 0.0) for _ in range(frames))
    wav = pcm_to_wav(pcm, 16000)
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.getnframes() == frames


def test_pcm_to_wav_clips_overshooting_samples():
    # float32 buffers can exceed +-1.0; out-of-range samples must be clamped,
    # not wrapped into loud noise when cast to int16. Clamping to +-1.0 then
    # scaling by 32767 (the translator's exact pcm_to_wav) yields +-32767.
    pcm = struct.pack("<f", 2.0) + struct.pack("<f", -2.0)
    wav = pcm_to_wav(pcm, 16000)
    with wave.open(io.BytesIO(wav), "rb") as w:
        data = w.readframes(2)
    assert struct.unpack("<hh", data) == (32767, -32767)

