from core.brain import evict_old_heartbeats, HEARTBEAT_WINDOW


def _msg(role, content, origin):
    return {"role": role, "content": content, "_origin": origin}


def _hb_block(idx: int) -> list[dict]:
    """A heartbeat assistant turn: user-heartbeat trigger + assistant reply."""
    return [
        _msg("user", "[heartbeat]", "heartbeat"),
        _msg("assistant", f"thought-{idx}", "heartbeat"),
    ]


def test_keeps_user_turns_evicts_old_heartbeats():
    messages = [_msg("system", "...", "boot"), _msg("user", "hi", "user"), _msg("assistant", "yo", "user")]
    for i in range(HEARTBEAT_WINDOW + 3):
        messages.extend(_hb_block(i))

    evict_old_heartbeats(messages)

    # System + user pair preserved
    assert messages[0]["_origin"] == "boot"
    assert messages[1]["content"] == "hi"
    assert messages[2]["content"] == "yo"

    # Only HEARTBEAT_WINDOW heartbeat blocks remain
    remaining_hb_assistants = [m for m in messages
                               if m["_origin"] == "heartbeat" and m["role"] == "assistant"]
    assert len(remaining_hb_assistants) == HEARTBEAT_WINDOW
    # Oldest ones were evicted: thought-0 gone, thought-(N+2) present
    contents = [m["content"] for m in remaining_hb_assistants]
    assert "thought-0" not in contents
    assert f"thought-{HEARTBEAT_WINDOW + 2}" in contents


def test_no_eviction_when_under_window():
    messages = [_msg("system", "...", "boot")]
    for i in range(HEARTBEAT_WINDOW - 1):
        messages.extend(_hb_block(i))
    before = len(messages)
    evict_old_heartbeats(messages)
    assert len(messages) == before
