"""Tests for chotu/voice.py — pure audio utilities."""

import numpy as np
import pytest


def test_audio_to_int16_range():
    from chotu.voice import _audio_to_int16
    # float32 in [-1, 1] should map to int16 range
    chunk = np.array([0.0, 1.0, -1.0, 0.5], dtype=np.float32)
    result = _audio_to_int16(chunk)
    assert result.dtype == np.int16
    assert result[0] == 0
    assert result[1] == 32767
    assert result[2] == -32768
    assert 16383 <= result[3] <= 16384


def test_audio_to_int16_clips_over_range():
    from chotu.voice import _audio_to_int16
    # Values slightly outside [-1, 1] shouldn't crash (clip to int16 bounds)
    chunk = np.array([1.5, -1.5], dtype=np.float32)
    result = _audio_to_int16(chunk)
    assert result.dtype == np.int16


def test_is_speech_loud_chunk():
    from chotu.voice import _is_speech
    # 0.5 amplitude = RMS 0.5, well above default 0.01 threshold
    loud = np.full(1280, 0.5, dtype=np.float32)
    assert _is_speech(loud) is True


def test_is_speech_silent_chunk():
    from chotu.voice import _is_speech
    silent = np.zeros(1280, dtype=np.float32)
    assert _is_speech(silent) is False


def test_is_speech_custom_threshold():
    from chotu.voice import _is_speech
    chunk = np.full(1280, 0.05, dtype=np.float32)
    assert _is_speech(chunk, threshold=0.1) is False
    assert _is_speech(chunk, threshold=0.01) is True
