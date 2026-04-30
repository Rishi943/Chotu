import asyncio


def test_build_goal_prompt_injects_goal():
    from chotu.system_prompt import build_goal_prompt
    prompt = build_goal_prompt("find the blue bottle")
    assert "find the blue bottle" in prompt


def test_build_goal_prompt_includes_auto_mode():
    from chotu.system_prompt import build_goal_prompt
    prompt = build_goal_prompt("any goal")
    assert "Goal Pursuit" in prompt


def test_build_system_prompt_reactive():
    from chotu.system_prompt import build_system_prompt
    prompt = build_system_prompt("reactive")
    assert "Reactive" in prompt


def test_build_system_prompt_falls_back_to_reactive():
    from chotu.system_prompt import build_system_prompt
    prompt = build_system_prompt("nonexistent_mode")
    assert "Reactive" in prompt


def test_local_goal_complete_returns_envelope():
    from chotu.tools import local_goal_complete, set_goal_complete_event
    event = asyncio.Event()
    set_goal_complete_event(event)

    result = asyncio.run(local_goal_complete("found it", True))

    assert result["ok"] is True
    assert result["tool"] == "goal_complete"
    assert result["result"]["outcome"] == "found it"
    assert result["result"]["success"] is True


def test_local_goal_complete_sets_event():
    from chotu.tools import local_goal_complete, set_goal_complete_event
    event = asyncio.Event()
    set_goal_complete_event(event)

    asyncio.run(local_goal_complete("done", True))

    assert event.is_set()


def test_local_goal_complete_result_accessible():
    from chotu.tools import local_goal_complete, set_goal_complete_event, _goal_complete_result
    event = asyncio.Event()
    set_goal_complete_event(event)

    asyncio.run(local_goal_complete("gave up", False))

    assert _goal_complete_result["outcome"] == "gave up"
    assert _goal_complete_result["success"] is False


def test_compress_vision_keeps_last_image():
    from chotu.brain import _compress_vision_in_history

    old_image_msg = {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            {"type": "text", "text": "camera view"},
        ],
    }
    new_image_msg = {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBBB"}},
            {"type": "text", "text": "newer view"},
        ],
    }
    messages = [old_image_msg, new_image_msg]
    _compress_vision_in_history(messages)

    # old image replaced with text placeholder
    assert isinstance(messages[0]["content"], str)
    assert "compressed" in messages[0]["content"]
    # new image untouched
    assert isinstance(messages[1]["content"], list)
    assert any(b.get("type") == "image_url" for b in messages[1]["content"])


def test_compress_vision_single_image_not_compressed():
    from chotu.brain import _compress_vision_in_history

    image_msg = {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            {"type": "text", "text": "camera view"},
        ],
    }
    messages = [image_msg]
    _compress_vision_in_history(messages)

    # only one image — not compressed (it's the most recent)
    assert isinstance(messages[0]["content"], list)
