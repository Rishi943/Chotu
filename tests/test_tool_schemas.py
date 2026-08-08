"""Five tools, and `act` routes push up to the trick, not the seated preset."""
import pytest
from core.tool_schemas import TOOL_SCHEMAS, ACT_NAMES, SENSE_KINDS

NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}


def test_exactly_five_tools():
    assert NAMES == {"move", "act", "sense", "say", "read"}


def test_no_retired_tools_survive():
    for gone in ("pose", "speak", "get_battery", "get_distance",
                 "set_face", "wait_for_event", "cast_spell", "do_trick"):
        assert gone not in NAMES


def test_move_directions_are_the_bridge_spelling():
    move = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "move")
    dirs = move["function"]["parameters"]["properties"]["direction"]["enum"]
    assert dirs == ["forward", "backward", "turn left", "turn right"]
    assert "left" not in dirs and "right" not in dirs


def test_push_up_routes_to_the_trick_not_the_pose():
    assert ACT_NAMES["push up"] == ("trick", "pushup")


def test_stand_and_sit_route_to_pose():
    assert ACT_NAMES["stand"] == ("pose", "stand")
    assert ACT_NAMES["sit"] == ("pose", "sit")


def test_dance_exists_because_the_model_already_asks_for_it():
    assert "dance" in ACT_NAMES


def test_act_enum_and_act_names_agree():
    act = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "act")
    assert set(act["function"]["parameters"]["properties"]["name"]["enum"]) == set(ACT_NAMES)


def test_sense_kinds():
    assert SENSE_KINDS == ("battery", "distance", "view")
