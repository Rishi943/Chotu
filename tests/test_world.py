import json
from pathlib import Path
import pytest

from core import world


@pytest.fixture(autouse=True)
def isolated_world(tmp_path, monkeypatch):
    """Each test gets its own data/world.json."""
    p = tmp_path / "world.json"
    monkeypatch.setattr(world, "WORLD_PATH", p)
    world._GRAPH = {"nodes": {}, "origin_node": None, "version": 1}
    yield p


def test_add_node_assigns_sequential_ids():
    a = world.add_node(0, 0, heading=0)
    b = world.add_node(1, 0, heading=0)
    assert a == "node-001"
    assert b == "node-002"


def test_first_node_is_origin():
    a = world.add_node(0, 0, heading=0)
    assert world.origin() == a


def test_add_photo_stored_under_node():
    nid = world.add_node(0, 0, heading=0)
    world.add_photo(nid, photo_idx=0, heading=0, description="d",
                    anchors_in_photo=["a"], objects_in_photo=[],
                    open_path=True, forward_steps=3, distance_cm=50)
    n = world.get_node(nid)
    assert len(n["photos"]) == 1
    assert n["photos"][0]["heading"] == 0
    assert n["photos"][0]["open_path"] is True


def test_add_photo_same_heading_replaces():
    nid = world.add_node(0, 0, heading=0)
    world.add_photo(nid, photo_idx=0, heading=0, description="first",
                    anchors_in_photo=[], objects_in_photo=[], open_path=False)
    world.add_photo(nid, photo_idx=0, heading=0, description="second",
                    anchors_in_photo=[], objects_in_photo=[], open_path=False)
    n = world.get_node(nid)
    assert len(n["photos"]) == 1
    assert n["photos"][0]["description"] == "second"


def test_add_exit_dedups():
    a = world.add_node(0, 0, heading=0)
    b = world.add_node(1, 0, heading=0)
    world.add_exit(a, heading=0, to_node=b, forward_steps=4)
    world.add_exit(a, heading=0, to_node=b, forward_steps=4)
    n = world.get_node(a)
    assert len(n["exits"]) == 1


def test_save_load_roundtrip(isolated_world):
    a = world.add_node(0, 0, heading=0)
    world.add_photo(a, photo_idx=0, heading=0, description="d",
                    anchors_in_photo=["x"], objects_in_photo=[], open_path=False)
    world.save()

    world._GRAPH = {"nodes": {}, "origin_node": None, "version": 1}
    world.load()
    n = world.get_node(a)
    assert n["photos"][0]["anchors_in_photo"] == ["x"]


def test_load_missing_file_starts_empty(isolated_world):
    assert not isolated_world.exists()
    world.load()
    assert world.list_nodes() == []


def test_load_corrupt_file_starts_empty(isolated_world):
    isolated_world.write_text("not json{")
    world.load()  # must not raise
    assert world.list_nodes() == []
