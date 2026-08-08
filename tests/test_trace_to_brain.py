"""trace_to_brain: O/T/A session traces -> brain-format SFT samples."""

import json
from pathlib import Path

import pytest

from scripts.robot.trace_to_brain import convert

REAL_SESSION = Path("out/sessions/2026-07-07_13-00-30_fable")


def _write_fixture(tmp_path: Path) -> Path:
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames/000.jpg").write_bytes(b"\xff\xd8fakejpg")
    recs = [
        {"ts": 1.0, "seq": 0, "observation": None, "thought": "waking up", "action": None},
        {"ts": 2.0, "seq": 1, "observation": None, "thought": None,
         "action": {"tool": "set_face", "args": {"name": "idle"},
                    "result": {"ok": True, "tool": "face", "result": {"name": "idle"},
                               "duration_ms": 0, "timestamp": 2.0, "error": None}}},
        {"ts": 3.0, "seq": 2, "observation": None, "thought": "look around", "action": None},
        {"ts": 4.0, "seq": 3, "thought": None, "action": None,
         "observation": {"tool": "capture_vision", "args": {}, "frame": "frames/000.jpg",
                         "result": {"ok": True, "tool": "capture", "result": {},
                                    "duration_ms": 1, "timestamp": 4.0, "error": None}}},
        {"ts": 5.0, "seq": 4, "observation": None, "thought": None,
         "action": {"tool": "move", "args": {"direction": "turn right", "steps": 2},
                    "result": {"ok": True, "tool": "move", "result": {"steps_completed": 2},
                               "duration_ms": 4000, "timestamp": 5.0, "error": None}}},
        {"ts": 6.0, "seq": 5, "observation": {"tool": "wait_for_event", "args": {"timeout": 10},
                                              "result": {"event": "timeout", "text": None,
                                                         "waited_s": 10.0}},
         "thought": None, "action": None},
        {"ts": 7.0, "seq": 6, "observation": {"tool": "wait_for_event", "args": {"timeout": 10},
                                              "result": {"event": "text", "text": "come here",
                                                         "waited_s": 1.0}},
         "thought": None, "action": None},
        {"ts": 8.0, "seq": 7, "observation": None, "thought": None,
         "action": {"tool": "speak", "args": {"text": "hello"},
                    "result": {"ok": True, "tool": "speak",
                               "result": {"text": "hello", "played": False, "muted": True},
                               "duration_ms": 0, "timestamp": 8.0, "error": None}}},
        {"ts": 9.0, "seq": 8, "observation": {"tool": "marker", "args": {"text": "ACT 1"},
                                              "result": {}},
         "thought": None, "action": None},
        {"ts": 10.0, "seq": 9, "observation": None, "thought": None,
         "action": {"tool": "set_face", "args": {"name": "sleeping"},
                    "result": {"ok": True, "tool": "face", "result": {"name": "sleeping"},
                               "duration_ms": 0, "timestamp": 10.0, "error": None}}},
    ]
    with (tmp_path / "trace.jsonl").open("w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    return tmp_path


def test_sample_per_turn_and_marker_sidecar(tmp_path):
    samples, markers = convert(_write_fixture(tmp_path))
    # turns: set_face, move, wait(timeout), wait(text), speak, set_face2
    # (capture + marker excluded)
    assert len(samples) == 6
    assert markers == [{"seq": 8, "text": "ACT 1"}]
    for s in samples:
        assert s["messages"][0]["role"] == "system"
        assert s["messages"][-1]["role"] == "assistant"
        assert s["messages"][-1]["tool_calls"]
        # target's tool_call id must not appear in any earlier message (no duplication)
        target_id = s["messages"][-1]["tool_calls"][0]["id"]
        for m in s["messages"][:-1]:
            if m.get("tool_calls"):
                assert all(tc["id"] != target_id for tc in m["tool_calls"])
            if m.get("tool_call_id"):
                assert m["tool_call_id"] != target_id


def test_thoughts_become_content(tmp_path):
    samples, _ = convert(_write_fixture(tmp_path))
    first = samples[0]["messages"][-1]
    assert first["content"] == "waking up"
    assert first["tool_calls"][0]["function"]["name"] == "set_face"
    # thought before the (dropped) capture carries to the move turn
    move = samples[1]["messages"][-1]
    assert move["content"] == "look around"


def test_capture_becomes_frame_tail_not_tool_call(tmp_path):
    samples, _ = convert(_write_fixture(tmp_path))
    move_sample = samples[1]
    names = [tc["function"]["name"]
             for m in move_sample["messages"] if m.get("tool_calls")
             for tc in m["tool_calls"]]
    assert "capture_vision" not in names
    frame_msgs = [m for m in move_sample["messages"]
                  if isinstance(m.get("content"), list)
                  and any(b.get("type") == "image_path" for b in m["content"])]
    assert frame_msgs, "frame tail missing"
    assert frame_msgs[-1]["content"][0]["path"] == "frames/000.jpg"


def test_wait_text_injects_human_message_and_envelope_wrap(tmp_path):
    samples, _ = convert(_write_fixture(tmp_path))
    speak_sample = samples[4]
    texts = [m["content"] for m in speak_sample["messages"]
             if m["role"] == "user" and isinstance(m["content"], str)]
    assert any("[human] come here" in t for t in texts)
    wait_results = [json.loads(m["content"]) for m in speak_sample["messages"]
                    if m["role"] == "tool"]
    wrapped = [r for r in wait_results if r.get("tool") == "wait_for_event"]
    assert wrapped and all(r.get("ok") is True and "result" in r for r in wrapped)


def test_speak_result_strips_mute_fields(tmp_path):
    samples, _ = convert(_write_fixture(tmp_path))
    # speak result appears in samples[5] (set_face2 turn), whose context includes
    # the completed speak turn from body
    tool_msgs = [json.loads(m["content"]) for m in samples[5]["messages"]
                 if m["role"] == "tool"]
    speak_envs = [r for r in tool_msgs if r.get("tool") == "speak"]
    assert speak_envs
    assert "muted" not in speak_envs[0]["result"]
    assert "played" not in speak_envs[0]["result"]


def test_state_block_appears_after_motion(tmp_path):
    samples, _ = convert(_write_fixture(tmp_path))
    # sample after the move turn (wait timeout turn) must carry a [STATE] user msg
    wait_sample = samples[2]
    states = [m for m in wait_sample["messages"]
              if m["role"] == "user" and isinstance(m["content"], str)
              and m["content"].startswith("[STATE]")]
    assert states
    assert "heading" in states[-1]["content"]


@pytest.mark.skipif(not REAL_SESSION.exists(), reason="real session not on disk")
def test_real_session_smoke():
    samples, markers = convert(REAL_SESSION)
    assert len(samples) > 100
    for s in samples[:5] + samples[-5:]:
        roles = [m["role"] for m in s["messages"]]
        assert roles[0] == "system" and roles[-1] == "assistant"
