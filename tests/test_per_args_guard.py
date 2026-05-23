"""Per-args fail guard: the same tool with different args may retry within a turn."""

import pytest
from core.brain import _guard_key, _should_suppress, _record_failure


def test_guard_key_differs_by_args():
    k1 = _guard_key("record_photo", {"anchors": ["a"], "open_path": True})
    k2 = _guard_key("record_photo", {"anchors": ["a"], "open_path": False})
    assert k1 != k2


def test_guard_key_stable_across_arg_order():
    k1 = _guard_key("move", {"direction": "turn left", "steps": 1})
    k2 = _guard_key("move", {"steps": 1, "direction": "turn left"})
    assert k1 == k2


def test_suppress_only_identical_call():
    state: set = set()
    _record_failure(state, "record_photo", {"open_path": True})
    assert _should_suppress(state, "record_photo", {"open_path": True}) is True
    assert _should_suppress(state, "record_photo", {"open_path": False}) is False
