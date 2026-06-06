from core.brain import _is_frame_msg, enforce_frame_window


def _frame():
    return {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
        {"type": "text", "text": "view"},
    ]}


def _text(role, txt):
    return {"role": role, "content": txt}


def _mem(n_frames):
    mem = []
    for i in range(n_frames):
        mem.append(_frame())
        mem.append(_text("assistant", f"desc{i}"))
    return mem


def test_is_frame_msg_true_for_image_user():
    assert _is_frame_msg(_frame()) is True


def test_is_frame_msg_false_for_text():
    assert _is_frame_msg(_text("user", "hi")) is False
    assert _is_frame_msg(_text("assistant", "yo")) is False


def test_keeps_only_last_4_frames():
    mem = _mem(6)
    enforce_frame_window(mem, keep=4)
    frames = [m for m in mem if _is_frame_msg(m)]
    assert len(frames) == 4
    # Older frames are deleted outright (no stub message left behind); only
    # the assistant descriptions persist as semantic context.
    assert all(m.get("_origin") != "frame_stripped" for m in mem)


def test_noop_when_at_or_under_keep():
    mem = _mem(4)
    before = [dict(m) for m in mem]
    enforce_frame_window(mem, keep=4)
    assert mem == before


def test_idempotent():
    mem = _mem(6)
    enforce_frame_window(mem, keep=4)
    once = [dict(m) for m in mem]
    enforce_frame_window(mem, keep=4)
    assert mem == once


def test_keep_zero_strips_all():
    mem = _mem(3)
    enforce_frame_window(mem, keep=0)
    assert [m for m in mem if _is_frame_msg(m)] == []
