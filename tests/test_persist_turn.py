from core.brain import persist_turn, _is_frame_msg


def _frame():
    return {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
        {"type": "text", "text": "view"},
    ]}


def test_empty_heartbeat_persists_nothing():
    mem = []
    persist_turn(mem, [{"role": "user", "content": "[heartbeat]"}], kind="heartbeat", iterations=0)
    assert mem == []


def test_normal_turn_appended():
    mem = []
    turn = [
        {"role": "user", "content": "walk"},
        {"role": "assistant", "content": "ok", "tool_calls": [{"id": "c1", "type": "function",
            "function": {"name": "move", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        {"role": "assistant", "content": "done"},
    ]
    persist_turn(mem, turn, kind="user", iterations=1)
    assert mem == turn


def test_frame_window_enforced_after_extend():
    # 5 frames already in memory, one more arrives this turn -> only 4 images kept
    mem = []
    for _ in range(5):
        mem.append(_frame())
        mem.append({"role": "assistant", "content": "d"})
    new_turn = [{"role": "user", "content": "[heartbeat]"}, _frame(),
                {"role": "assistant", "content": "d6"}]
    persist_turn(mem, new_turn, kind="heartbeat", iterations=1)
    assert len([m for m in mem if _is_frame_msg(m)]) == 4
