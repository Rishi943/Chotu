from starlette.testclient import TestClient
from scripts.animation.animation_studio import app, _ANIM_DIR

client = TestClient(app)


def test_list_includes_builtin_and_user():
    r = client.get("/animations")
    assert r.status_code == 200
    names = {a["tool"] for a in r.json()["animations"]}
    assert "forward" in names          # built-in
    builtins = [a for a in r.json()["animations"] if a["builtin"]]
    assert builtins and all("frames" in a for a in builtins)


def test_save_writes_user_file_then_lists():
    payload = {"tool": "unit_test_anim", "description": "x", "persona_gated": False,
               "default_speed": 60, "frames": [{"legs": [[45, 45, -50], [45, 0, -50], [45, 0, -50], [45, 45, -50]],
               "speed": 60, "hold_s": 0}]}
    r = client.post("/animations", json=payload)
    assert r.status_code == 200 and r.json()["ok"]
    try:
        names = {a["tool"] for a in client.get("/animations").json()["animations"]}
        assert "unit_test_anim" in names
    finally:
        (_ANIM_DIR / "unit_test_anim.json").unlink(missing_ok=True)


def test_save_rejects_bad_tool_name():
    r = client.post("/animations", json={"tool": "Bad Name", "frames": [{"legs": []}]})
    assert r.status_code == 400


def test_save_rejects_path_escape():
    r = client.post("/animations", json={"tool": "../evil",
        "frames": [{"legs": [[1, 1, 1], [1, 1, 1], [1, 1, 1], [1, 1, 1]]}]})
    assert r.status_code in (400, 403)
