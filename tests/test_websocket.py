"""Studio websocket API (client and weather patched)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from custom_components.ai_prime.const import (
    CHANNELS,
    CONF_ACTIVE_PRESET,
    CONF_FOLLOW,
    CONF_PRESETS,
    CONF_SUN_LOCATION,
    CONF_SUN_MODE,
)
from custom_components.ai_prime.geocode import GeocodeError
from custom_components.ai_prime.nature.weather import Weather, WeatherSource

from .test_studio import _add, _entry_options
from .conftest import setup_entry

POINTS = [
    {"t": 0, "v": {c: 10.0 for c in CHANNELS}},
    {"t": 720, "v": {c: 50.5 for c in CHANNELS}},
]


def _opts(level: int = 300) -> dict:
    return _entry_options(level, "Lvl")


async def _call(client, type_: str, **kwargs) -> dict:
    await client.send_json_auto_id({"type": f"ai_prime/{type_}", **kwargs})
    return await client.receive_json()


@pytest.fixture
async def ws(hass, hass_ws_client, stop_patches):
    entry, _, _ = await setup_entry(hass, stop_patches, options=_opts())
    return entry, await hass_ws_client(hass)


async def test_entries(hass, ws) -> None:
    entry, client = ws
    res = await _call(client, "entries")
    assert res["success"]
    assert res["result"] == [
        {"entry_id": entry.entry_id, "title": entry.title, "loaded": True}
    ]


async def test_config_get(hass, ws) -> None:
    entry, client = ws
    res = (await _call(client, "config/get", entry_id=entry.entry_id))["result"]
    assert [p["id"] for p in res["presets"]] == ["a"]
    assert res["presets"][0]["name"] == "Lvl"
    assert res["presets"][0]["readonly"] is False
    assert res["presets"][0]["points"][0] == {"t": 0, "v": {c: 30.0 for c in CHANNELS}}
    assert res["active"] == "a"
    assert res["effective_active"] == "a"
    assert res["effective_active_name"] == "Lvl"
    assert res["follow"] is None
    assert res["followable"] == []
    assert res["lamp_write_pending"] is False
    home = {"latitude": hass.config.latitude, "longitude": hass.config.longitude}
    assert res["home"] == home
    assert res["locations"]["sun_location"] == home
    assert res["locations"]["sun_mode"] == "anchored"
    assert res["effective_locations"] == res["locations"]


async def test_templates_listed(hass, ws) -> None:
    entry, client = ws
    res = (await _call(client, "config/get", entry_id=entry.entry_id))["result"]
    by_id = {t["id"]: t for t in res["templates"]}
    assert list(by_id) == ["natur", "leer"]  # original only when captured
    assert len(by_id["natur"]["points"]) == 12
    assert by_id["natur"]["points"][0]["t"] == 360
    assert [p["t"] for p in by_id["leer"]["points"]] == [0, 1439]
    assert all(v == 0 for v in by_id["leer"]["points"][0]["v"].values())

    original = {"name": "Original (Lampe)", "readonly": True,
                "ramps": {c: [[0, 100], [600, 500]] for c in CHANNELS}}
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_PRESETS: {**entry.options[CONF_PRESETS], "original": original}}
    )
    res = (await _call(client, "config/get", entry_id=entry.entry_id))["result"]
    by_id = {t["id"]: t for t in res["templates"]}
    assert list(by_id) == ["natur", "original", "leer"]
    assert by_id["original"]["points"][1] == {"t": 600, "v": {c: 50.0 for c in CHANNELS}}


async def test_config_get_unknown_entry(hass, ws) -> None:
    _, client = ws
    res = await _call(client, "config/get", entry_id="nope")
    assert res["error"]["code"] == "not_found"


async def test_not_loaded_entry_is_not_found(hass, ws) -> None:
    entry, client = ws
    await hass.config_entries.async_unload(entry.entry_id)
    res = await _call(client, "config/get", entry_id=entry.entry_id)
    assert res["error"]["code"] == "not_found"
    res = (await _call(client, "entries"))["result"]
    assert res[0]["loaded"] is False


async def test_preset_save_new_and_update(hass, ws) -> None:
    entry, client = ws
    res = await _call(
        client, "preset/save", entry_id=entry.entry_id, name="My Preset", points=POINTS
    )
    assert res["success"] and res["result"] == {"id": "my_preset"}
    stored = entry.options[CONF_PRESETS]["my_preset"]
    assert stored["ramps"]["blue"] == [[0, 100], [720, 505]]
    assert entry.options[CONF_ACTIVE_PRESET] == "a"

    res = await _call(
        client, "preset/save", entry_id=entry.entry_id, preset_id="my_preset",
        name="Renamed", points=POINTS, activate=True,
    )
    assert res["result"] == {"id": "my_preset"}
    assert entry.options[CONF_ACTIVE_PRESET] == "my_preset"
    assert entry.options[CONF_PRESETS]["my_preset"]["name"] == "Renamed"


async def test_preset_save_writes_lamp_via_studio(hass, stop_patches, hass_ws_client) -> None:
    entry, _, _ = await setup_entry(hass, stop_patches, options=_opts())
    client = await hass_ws_client(hass)
    from custom_components.ai_prime.coordinator import PrimeClient

    PrimeClient.set_schedule_points.reset_mock()
    await _call(client, "preset/save", entry_id=entry.entry_id, preset_id="a",
                name="Lvl", points=POINTS, activate=True)
    assert PrimeClient.set_schedule_points.await_count == 1


async def test_lamp_rejected_is_reported_but_saved(hass, ws) -> None:
    entry, client = ws
    from custom_components.ai_prime.coordinator import LampWrite, PrimeCoordinator

    with patch.object(
        PrimeCoordinator, "write_active_schedule", AsyncMock(return_value=LampWrite.REJECTED)
    ):
        res = await _call(client, "preset/save", entry_id=entry.entry_id, name="New",
                          points=POINTS, activate=True)
    assert res["error"]["code"] == "lamp_rejected"
    assert entry.options[CONF_ACTIVE_PRESET] == "new"  # saving in HA is never blocked


async def test_preset_save_too_many_points(hass, ws) -> None:
    entry, client = ws
    points = [{"t": t, "v": POINTS[0]["v"]} for t in range(33)]
    res = await _call(client, "preset/save", entry_id=entry.entry_id, name="X", points=points)
    assert res["error"]["code"] == "invalid_points"


@pytest.mark.parametrize(
    ("points", "code"),
    [
        ([POINTS[0]], "invalid_points"),
        ([POINTS[0], {"t": 720, "v": {c: 500 for c in CHANNELS}}], "invalid_points"),
        ([POINTS[0], POINTS[0]], "invalid_points"),
        ("nope", "invalid_format"),
    ],
)
async def test_preset_save_invalid_points(hass, ws, points, code) -> None:
    entry, client = ws
    res = await _call(client, "preset/save", entry_id=entry.entry_id, name="X", points=points)
    assert res["error"]["code"] == code


async def test_preset_save_nan_rejected(hass, ws) -> None:
    entry, client = ws
    # JSON cannot carry NaN; call the handler path through the library directly.
    from custom_components.ai_prime.library import LibraryError, points_to_ramps

    bad = [POINTS[0], {"t": 5, "v": {**POINTS[1]["v"], "blue": float("nan")}}]
    with pytest.raises(LibraryError) as err:
        points_to_ramps(bad)
    assert err.value.code == "invalid_points"


@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
async def test_preset_save_invalid_name(hass, ws, name) -> None:
    entry, client = ws
    res = await _call(client, "preset/save", entry_id=entry.entry_id, name=name, points=POINTS)
    assert res["error"]["code"] == "invalid_name"


async def test_preset_save_unknown_id(hass, ws) -> None:
    entry, client = ws
    res = await _call(client, "preset/save", entry_id=entry.entry_id, preset_id="zzz",
                      name="X", points=POINTS)
    assert res["error"]["code"] == "not_found"


async def test_preset_delete(hass, ws) -> None:
    entry, client = ws
    res = await _call(client, "preset/delete", entry_id=entry.entry_id, preset_id="a")
    assert res["error"]["code"] == "in_use"
    await _call(client, "preset/save", entry_id=entry.entry_id, name="Other", points=POINTS)
    res = await _call(client, "preset/delete", entry_id=entry.entry_id, preset_id="other")
    assert res["success"]
    assert "other" not in entry.options[CONF_PRESETS]


async def test_preset_rename(hass, ws) -> None:
    entry, client = ws
    res = await _call(client, "preset/rename", entry_id=entry.entry_id, preset_id="a", name="New")
    assert res["success"]
    assert entry.options[CONF_PRESETS]["a"]["name"] == "New"
    res = await _call(client, "preset/rename", entry_id=entry.entry_id, preset_id="a", name=" ")
    assert res["error"]["code"] == "invalid_name"
    res = await _call(client, "preset/rename", entry_id=entry.entry_id, preset_id="a", name="x" * 61)
    assert res["error"]["code"] == "invalid_name"
    res = await _call(client, "preset/rename", entry_id=entry.entry_id, preset_id="a", name="x" * 60)
    assert res["success"]
    res = await _call(client, "preset/rename", entry_id=entry.entry_id, preset_id="zz", name="N")
    assert res["error"]["code"] == "not_found"


async def test_preset_activate(hass, ws) -> None:
    entry, client = ws
    await _call(client, "preset/save", entry_id=entry.entry_id, name="Other", points=POINTS)
    res = await _call(client, "preset/activate", entry_id=entry.entry_id, preset_id="other")
    assert res["success"]
    assert entry.options[CONF_ACTIVE_PRESET] == "other"
    res = await _call(client, "preset/activate", entry_id=entry.entry_id, preset_id="zz")
    assert res["error"]["code"] == "not_found"


async def test_locations_set(hass, ws) -> None:
    entry, client = ws
    loc = {"latitude": 47.37, "longitude": 8.54, "name": "Zürich"}
    res = await _call(client, "locations/set", entry_id=entry.entry_id,
                      locations={"sun_location": loc, "sun_mode": "real", "moon_location": None})
    assert res["success"]
    assert entry.options[CONF_SUN_LOCATION] == loc
    assert entry.options[CONF_SUN_MODE] == "real"
    got = (await _call(client, "config/get", entry_id=entry.entry_id))["result"]
    assert got["locations"]["sun_location"]["latitude"] == 47.37
    assert got["locations"]["moon_location"] == {
        "latitude": hass.config.latitude, "longitude": hass.config.longitude}


@pytest.mark.parametrize(
    "locations",
    [{"bogus": 1}, {"sun_mode": "x"}, {"sun_location": {"latitude": 99, "longitude": 0}},
     {"sun_location": {"latitude": "nan", "longitude": 0}}],
)
async def test_locations_set_invalid(hass, ws, locations) -> None:
    entry, client = ws
    res = await _call(client, "locations/set", entry_id=entry.entry_id, locations=locations)
    assert res["error"]["code"] == "invalid_format"


async def test_config_get_light_defaults(hass, ws) -> None:
    entry, client = ws
    res = (await _call(client, "config/get", entry_id=entry.entry_id))["result"]
    assert res["light"] == {"brightness": 100, "night_light": 0.0, "moon_max": 3}


async def test_light_set(hass, ws) -> None:
    entry, client = ws
    res = await _call(client, "light/set", entry_id=entry.entry_id,
                      brightness=40, night_light=1.26, moon_max=2)
    assert res["success"]
    assert res["result"] == {"brightness": 40, "night_light": 1.3, "moon_max": 2.0}
    assert entry.options["brightness"] == 40
    assert entry.options["night_light"] == 1.3
    got = (await _call(client, "config/get", entry_id=entry.entry_id))["result"]
    assert got["light"]["brightness"] == 40
    assert entry.runtime_data.nature.settings.brightness == 0.4
    assert entry.runtime_data.nature.settings.night_level == 13
    pv = (await _call(client, "preview", entry_id=entry.entry_id))["result"]
    assert pv["light"]["brightness"] == 40
    assert "theory" in pv["curve"]


@pytest.mark.parametrize(
    "extra",
    [{"brightness": 0}, {"brightness": 101}, {"brightness": 50.5}, {"night_light": 5.1},
     {"night_light": -1}, {"moon_max": 6}],
)
async def test_light_set_invalid(hass, ws, extra) -> None:
    entry, client = ws
    res = await _call(client, "light/set", entry_id=entry.entry_id, **extra)
    assert res["error"]["code"] == "invalid_format"
    assert "brightness" not in entry.options


async def test_follow_set_and_followable(hass, stop_patches, hass_ws_client) -> None:
    leader, _, _ = await setup_entry(hass, stop_patches, options=_opts(300))
    follower = await _add(hass, stop_patches, "FOLLOWER0001", _entry_options(900, "Own"))
    client = await hass_ws_client(hass)
    got = (await _call(client, "config/get", entry_id=follower.entry_id))["result"]
    assert got["followable"] == [{"entry_id": leader.entry_id, "title": leader.title}]

    res = await _call(client, "follow/set", entry_id=follower.entry_id, leader=leader.entry_id)
    assert res["success"]
    assert follower.options[CONF_FOLLOW] == leader.entry_id
    got = (await _call(client, "config/get", entry_id=follower.entry_id))["result"]
    assert got["follow"] == leader.entry_id
    leader_got = (await _call(client, "config/get", entry_id=leader.entry_id))["result"]
    assert got["effective_active_name"] == leader_got["presets"][0]["name"] == "Lvl"
    assert got["effective_active"] == "a"

    res = await _call(client, "follow/set", entry_id=follower.entry_id, leader=follower.entry_id)
    assert res["error"]["code"] == "follow_cycle"
    res = await _call(client, "follow/set", entry_id=follower.entry_id, leader="nope")
    assert res["error"]["code"] == "not_found"
    res = await _call(client, "follow/set", entry_id=follower.entry_id, leader=None)
    assert res["success"] and follower.options[CONF_FOLLOW] is None


NOW = datetime(2026, 6, 21, 10, 0, tzinfo=timezone.utc)


def _weather(cloud=0.5, storm=False, available=True):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch.object(WeatherSource, "get", AsyncMock(return_value=Weather(cloud, storm, NOW)))
    )
    stack.enter_context(
        patch.object(WeatherSource, "available", PropertyMock(return_value=available))
    )
    return stack


async def test_preview_today_has_weather(hass, ws) -> None:
    entry, client = ws
    with _weather(0.4, True):
        res = await _call(client, "preview", entry_id=entry.entry_id)
    assert res["success"]
    out = res["result"]
    assert out["weather"] == {"cloud_cover": 40, "thunderstorm": True, "code": 95}
    assert len(out["curve"]["t"]) == 145
    assert set(out["curve"]["base"]) == set(CHANNELS)
    assert out["sun"]["home"] is not None


async def test_preview_other_date_no_weather(hass, ws) -> None:
    entry, client = ws
    with _weather() as _:
        res = await _call(client, "preview", entry_id=entry.entry_id, date="2030-01-15")
        assert WeatherSource.get.await_count == 0
    assert res["result"]["date"] == "2030-01-15"
    assert res["result"]["weather"] is None


async def test_preview_weather_failure_is_null(hass, ws) -> None:
    entry, client = ws
    with _weather(available=False):
        res = await _call(client, "preview", entry_id=entry.entry_id)
    assert res["success"] and res["result"]["weather"] is None


@pytest.mark.parametrize("value", ["tomorrow", "2026-13-01", "2026-1-1", "20260101"])
async def test_preview_invalid_date(hass, ws, value) -> None:
    entry, client = ws
    res = await _call(client, "preview", entry_id=entry.entry_id, date=value)
    assert res["error"]["code"] == "invalid_format"


async def test_preview_unknown_entry(hass, ws) -> None:
    _, client = ws
    res = await _call(client, "preview", entry_id="nope")
    assert res["error"]["code"] == "not_found"


async def test_geocode(hass, ws) -> None:
    _, client = ws
    hits = [{"name": "Zürich", "country": "CH", "admin1": "ZH", "latitude": 47.3667, "longitude": 8.55}]
    with patch("custom_components.ai_prime.websocket_api.geocode", AsyncMock(return_value=hits)) as g:
        res = await _call(client, "geocode", query="Zürich")
    assert res["result"] == hits
    assert g.await_args.args[1:] == ("Zürich", "en")
    hass.config.language = "de-CH"
    with patch("custom_components.ai_prime.websocket_api.geocode", AsyncMock(return_value=[])) as g:
        await _call(client, "geocode", query="Bern")
    assert g.await_args.args[2] == "de"


async def test_geocode_failure(hass, ws) -> None:
    _, client = ws
    with patch("custom_components.ai_prime.websocket_api.geocode",
               AsyncMock(side_effect=GeocodeError("boom"))):
        res = await _call(client, "geocode", query="Zürich")
    assert res["error"]["code"] == "geocode_failed"


async def test_geocode_query_too_long(hass, ws) -> None:
    _, client = ws
    with patch("custom_components.ai_prime.websocket_api.geocode", AsyncMock()) as g:
        res = await _call(client, "geocode", query="x" * 101)
    assert res["error"]["code"] == "invalid_format"
    g.assert_not_awaited()


WRITE_COMMANDS = [
    ("locations/set", {"locations": {}}),
    ("light/set", {"brightness": 50}),
    ("preset/save", {"name": "X", "points": POINTS}),
    ("preset/delete", {"preset_id": "a"}),
    ("preset/rename", {"preset_id": "a", "name": "X"}),
    ("preset/activate", {"preset_id": "a"}),
    ("follow/set", {"leader": None}),
    ("demo/frame", {"values": {c: 10.0 for c in CHANNELS}}),
    ("demo/stop", {}),
]


async def test_write_commands_require_admin(
    hass, stop_patches, hass_ws_client, hass_read_only_access_token
) -> None:
    entry, _, _ = await setup_entry(hass, stop_patches, options=_opts())
    client = await hass_ws_client(hass, hass_read_only_access_token)
    for command, extra in WRITE_COMMANDS:
        res = await _call(client, command, entry_id=entry.entry_id, **extra)
        assert res["error"]["code"] == "unauthorized", command
    assert entry.options[CONF_PRESETS].keys() == {"a"}
    # Read commands stay available to non-admins.
    assert (await _call(client, "config/get", entry_id=entry.entry_id))["success"]


async def test_commands_registered_once(hass, ws) -> None:
    from custom_components.ai_prime import async_setup

    assert await async_setup(hass, {})  # second call must not raise or re-register
