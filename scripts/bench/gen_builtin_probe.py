import json
import re
import pathlib
import subprocess
import sys

SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILTIN = ROOT / "assets" / "Animations" / "builtin"


def test_generate_and_validate():
    subprocess.run([sys.executable, "-m", "scripts.animation.gen_builtin_animations"],
                   cwd=ROOT, check=True)
    files = list(BUILTIN.glob("*.json"))
    expected = {"forward", "backward", "turn_left", "turn_right", "wave", "sit", "stand",
                "look_up", "look_down", "look_left", "look_right", "push_up",
                "twist", "swimming", "handwork"}
    assert {f.stem for f in files} == expected
    for f in files:
        d = json.loads(f.read_text())
        assert SNAKE.match(d["tool"]), f"{f.name} tool not snake_case"
        assert d["frames"], f"{f.name} has no frames"
        for fr in d["frames"]:
            assert len(fr["legs"]) == 4, f"{f.name} frame needs 4 legs"
            for leg in fr["legs"]:
                assert len(leg) == 3, f"{f.name} leg must be [x,y,z]"
                assert all(isinstance(v, int) for v in leg), f"{f.name} legs must be ints"
