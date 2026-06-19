import pathlib
from scripts.animation.render_animation import render, support_ok
from scripts.animation.validate_animation import STAND

def _anim(frames): return {"tool":"t","description":"d","default_speed":60,"frames":frames}

def test_render_writes_png(tmp_path):
    a=_anim([{"legs":[list(l) for l in STAND],"speed":60,"hold_s":0}])
    out=render(a, tmp_path/"p.png", stability=True)
    assert out.exists() and out.stat().st_size > 0

def test_stand_is_stable():
    assert support_ok([list(l) for l in STAND]) is True
