def test_build_loop_messages_order():
    from core.brain import build_loop_messages
    memory = [
        {"role": "user", "content": "[boot] hi", "_origin": "boot"},
        {"role": "assistant", "content": "ok"},
    ]
    frame_stack = [{"image_b64": "c", "motion": ""}]
    msgs = build_loop_messages("SYS", memory, frame_stack)

    assert msgs[0] == {"role": "system", "content": "SYS"}
    # internal _origin fields stripped before sending
    assert all("_origin" not in m for m in msgs)
    # frames sit at the tail, after memory
    assert msgs[-1]["content"][1]["text"] == "[frame 0 | NOW — current view]"
    # memory content preserved in the middle
    assert {"role": "user", "content": "[boot] hi"} in msgs
