import json, pathlib, pytest
from scripts.animation.validate_animation import validate, install, STAND

def _anim(frames, tool="t"): return {"tool":tool,"description":"d","default_speed":60,"frames":frames}
def F(legs): return {"legs":legs,"speed":60,"hold_s":0}

def test_good_passes_no_errors():
    a=_anim([F([[45,45,-50],[45,0,-50],[45,0,-50],[45,45,-50]])])
    assert [i for i in validate(a) if i.severity=="ERROR"]==[]

def test_unreachable_foot_errors():
    a=_anim([F([[999,0,0],[45,0,-50],[45,0,-50],[45,45,-50]])])
    errs=[i for i in validate(a) if i.severity=="ERROR"]
    assert any("FR" in i.msg for i in errs)

def test_non_stand_end_warns():
    a=_anim([F([[45,0,-50]]*4)])
    assert any(i.severity=="WARN" and "stand" in i.msg.lower() for i in validate(a))

def test_bad_tool_name_errors():
    a=_anim([F([STAND[0],STAND[1],STAND[2],STAND[3]])], tool="Bad Name")
    assert any(i.severity=="ERROR" for i in validate(a))

def test_install_refuses_errors(tmp_path):
    a=_anim([F([[999,0,0]]*4)])
    with pytest.raises(ValueError): install(a, anim_dir=tmp_path)

def test_install_writes(tmp_path):
    a=_anim([F([list(l) for l in STAND])])
    p=install(a, anim_dir=tmp_path)
    assert p.exists() and json.loads(p.read_text())["tool"]=="t"
