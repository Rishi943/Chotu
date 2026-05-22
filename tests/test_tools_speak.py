"""Unit tests for the speak tool."""

import asyncio
from unittest.mock import patch

import pytest

from core.tools import TOOL_SCHEMAS, build_dispatch


def test_speak_tool_schema_registered():
    names = [s["function"]["name"] for s in TOOL_SCHEMAS]
    assert "speak" in names, f"speak tool not registered. Got: {names}"


def test_speak_tool_schema_shape():
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "speak")
    params = schema["function"]["parameters"]
    assert "text" in params["properties"]
    assert "text" in params["required"]


def test_speak_in_dispatch_map():
    class _DummyPi: pass
    estop = asyncio.Event()
    dispatch = build_dispatch(_DummyPi(), estop)
    assert "speak" in dispatch
