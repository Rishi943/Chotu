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
