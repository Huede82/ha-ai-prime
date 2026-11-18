"""Tests for the preset library core."""

import copy

import pytest

from custom_components.ai_prime.const import CHANNELS
from custom_components.ai_prime.library import (
    NATUR_ID,
    LibraryError,
    activate_preset,
    check_follow,
    delete_preset,
    effective_options,
    migrate_v1,
    new_preset_id,
    points_to_ramps,
    ramps_to_points,
    rename_preset,
    save_preset,
    to_lamp_schedule,
)
from custom_components.ai_prime.preset import Preset

RAMPS = {c: ((0, 0), (720, 500), (1439, 0)) for c in CHANNELS}


def _pt(t, **over):
    v = {c: 10.0 for c in CHANNELS}
    v.update(over)
    return {"t": t, "v": v}


def test_points_roundtrip_lossless():
    ramps = {c: ((0, 0), (1000, 1000)) for c in CHANNELS}
    ramps["blue"] = ((0, 0), (500, 400), (1000, 1000))
    out = ramps_to_points(ramps)
    assert [p["t"] for p in out] == [0, 500, 1000]
    back = points_to_ramps(out)
    a, b = Preset("x", ramps), Preset("x", back)
    for m in (0, 250, 500, 750, 1000):
        assert a.value_at(m) == b.value_at(m)


@pytest.mark.parametrize(
    "points",
    [
        [_pt(0), _pt(0)],
        [_pt(0)],
        [_pt(0), _pt(10, blue=100.05)],
        [_pt(0), _pt(10, blue=-1)],
        [_pt(0), _pt(10, blue="x")],
        [_pt(0), {"t": 10, "v": {"blue": 1.0}}],
        [_pt(0), _pt(1441)],
        [_pt(0), _pt(10, blue=float("nan"))],
        [_pt(0), _pt(10, blue=float("inf"))],
        [_pt(0), _pt(10, blue=float("-inf"))],
    ],
)
def test_points_rejects(points):
    with pytest.raises(LibraryError) as err:
        points_to_ramps(points)
    assert err.value.code == "invalid_points"
    assert isinstance(err.value.detail, str)


def test_migrate_v1_named_and_default():
    stored = {"name": "default", "ramps": {"blue": [[0, 0], [10, 5]]}}
    opts = {"preset": stored, "sun_mode": "x"}
    snapshot = copy.deepcopy(opts)
    new = migrate_v1(opts)
    assert opts == snapshot
    assert "preset" not in new and new["sun_mode"] == "x"
    assert new["presets"][NATUR_ID] == {
        "name": "Natur", "ramps": stored["ramps"], "readonly": False}
    assert new["active_preset"] == NATUR_ID
    assert new["follow"] is None
    assert new["lamp_write_pending"] is False
    assert new["original_captured"] is False
    assert migrate_v1({"preset": {**stored, "name": "Sommer"}})["presets"][
        NATUR_ID]["name"] == "Sommer"
    empty = migrate_v1({})
    assert empty["presets"] == {} and empty["active_preset"] is None
    assert migrate_v1({"preset": stored}, name="Lampe")["presets"][NATUR_ID]["name"] == "Lampe"


def test_points_max_32():
    points_to_ramps([_pt(t) for t in range(32)])
    with pytest.raises(LibraryError) as err:
        points_to_ramps([_pt(t) for t in range(33)])
    assert err.value.code == "invalid_points"
    assert err.value.detail == "max 32 points"


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "X"},
        {"name": "X", "ramps": None},
        {"name": "X", "ramps": {"blue": [[0]]}},
        {"name": "X", "ramps": {"blue": [["a", 1]]}},
        {"name": "X", "ramps": [1, 2]},
    ],
)
def test_effective_options_corrupt_active_preset(entry):
    eff = effective_options({"presets": {"a": entry}, "active_preset": "a"}, None)
    assert "preset" not in eff


def test_save_new_update_activate_readonly():
    opts = {"presets": {"original": {"name": "Original", "ramps": {}, "readonly": True}}}
    new, pid = save_preset(opts, None, "Sommer Tag", RAMPS, activate=True)
    assert pid == "sommer_tag" and new["active_preset"] == pid
    assert "sommer_tag" not in opts["presets"]
    new2, pid2 = save_preset(new, pid, "Sommer", RAMPS, activate=False)
    assert pid2 == pid and new2["presets"][pid]["name"] == "Sommer"
    with pytest.raises(LibraryError) as err:
        save_preset(new, "original", "x", RAMPS, activate=False)
    assert err.value.code == "readonly"
    with pytest.raises(LibraryError) as err:
        save_preset(new, "nope", "x", RAMPS, activate=False)
    assert err.value.code == "not_found"
    assert rename_preset(new, pid, "Neu")["presets"][pid]["name"] == "Neu"
    assert activate_preset({**new, "active_preset": None}, pid)["active_preset"] == pid
    with pytest.raises(LibraryError):
        activate_preset(new, "nope")


def test_delete_refuses_active_and_readonly():
    opts = {
        "presets": {
            "a": {"name": "A", "ramps": {}, "readonly": False},
            "b": {"name": "B", "ramps": {}, "readonly": False},
            "original": {"name": "O", "ramps": {}, "readonly": True},
        },
        "active_preset": "a",
    }
    with pytest.raises(LibraryError) as err:
        delete_preset(opts, "a")
    assert err.value.code == "in_use"
    with pytest.raises(LibraryError) as err:
        delete_preset(opts, "original")
    assert err.value.code == "readonly"
    with pytest.raises(LibraryError) as err:
        delete_preset(opts, "zzz")
    assert err.value.code == "not_found"
    assert "b" not in delete_preset(opts, "b")["presets"]
    assert "b" in opts["presets"]


def test_new_preset_id_collision():
    assert new_preset_id("Natur", ["natur"]) == "natur_2"
    assert new_preset_id("Natur", ["natur", "natur_2"]) == "natur_3"
    assert new_preset_id("!!!", []) == "preset"


def _lib(name, active=True):
    return {
        "presets": {"p": {"name": name, "ramps": {"blue": [[0, 1], [10, 2]]},
                          "readonly": False}},
        "active_preset": "p" if active else None,
    }


def test_effective_options_follower_uses_leader():
    own = {**_lib("Own"), "follow": "L", "sun_mode": "own", "weather_location": "own"}
    leader = {**_lib("Lead"), "sun_mode": "lead", "moon_location": "m"}
    eff = effective_options(own, leader)
    assert eff["preset"]["name"] == "Lead"
    assert eff["sun_mode"] == "lead" and eff["moon_location"] == "m"
    assert "weather_location" not in eff
    assert own["sun_mode"] == "own"


def test_effective_options_no_leader_is_own():
    own = {**_lib("Own"), "sun_mode": "own"}
    eff = effective_options(own, None)
    assert eff["preset"] == {"name": "Own", "ramps": {"blue": [[0, 1], [10, 2]]}}
    assert eff["sun_mode"] == "own"
    assert "preset" not in effective_options(_lib("x", active=False), None)


def test_check_follow_cycles():
    check_follow("a", "b", {"a": None, "b": None})
    with pytest.raises(LibraryError) as err:
        check_follow("a", "a", {})
    assert err.value.code == "follow_cycle"
    with pytest.raises(LibraryError):
        check_follow("a", "b", {"b": "c"})
    with pytest.raises(LibraryError):
        check_follow("a", "b", {"c": "a"})


def test_to_lamp_schedule_clips_1440_and_empty_channel():
    preset = Preset("P", {
        "blue": ((0, 0), (1439, 5), (1440, 7)),
        "green": ((0, 1), (1440, 9)),
    })
    out = to_lamp_schedule(preset)
    assert out["name"] == "P"
    by = {r["color"]: r["points"] for r in out["ramps"]}
    assert set(by) == set(CHANNELS)
    assert by["blue"] == [{"time": 0, "intensity": 0}, {"time": 1439, "intensity": 7}]
    assert by["green"] == [{"time": 0, "intensity": 1}, {"time": 1439, "intensity": 9}]
    assert by["deep_red"] == [{"time": 0, "intensity": 0}]
