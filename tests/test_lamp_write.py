"""Lamp schedule write, original capture and pending retry (against the fake lamp)."""

from __future__ import annotations

from unittest.mock import patch

import asyncio

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ai_prime.const import (
    CHANNELS,
    CONF_ACTIVE_PRESET,
    CONF_FOLLOW,
    CONF_LAMP_WRITE_PENDING,
    CONF_ORIGINAL_CAPTURED,
    CONF_PRESETS,
    DOMAIN,
)

from custom_components.ai_prime.coordinator import RETRY_MAX, RETRY_MIN
from custom_components.ai_prime.library import LibraryError

from .fake_lamp import make_fake_lamp, POINTS

MONOTONIC = "custom_components.ai_prime.coordinator.monotonic"
POST = ("POST", "/api/schedule")
UNREACHABLE = (500, "boom", {})  # HTTP error -> PrimeConnectionError


def _options(level: int = 300, name: str = "Lvl", end: int = 1440) -> dict:
    ramps = {c: [[0, level], [end, level]] for c in CHANNELS}
    return {
        CONF_PRESETS: {"a": {"name": name, "ramps": ramps, "readonly": False}},
        CONF_ACTIVE_PRESET: "a",
        CONF_FOLLOW: None,
        CONF_LAMP_WRITE_PENDING: False,
        CONF_ORIGINAL_CAPTURED: False,
    }


async def _add(hass, server, serial: str, options: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=serial,
        data={CONF_HOST: f"{server.host}:{server.port}"},
        options=options,
        version=2,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _with_b(hass, entry) -> None:
    ramps = {c: [[0, 700], [720, 700]] for c in CHANNELS}
    presets = {**entry.options[CONF_PRESETS], "b": {"name": "B", "ramps": ramps, "readonly": False}}
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_PRESETS: presets})
    await hass.async_block_till_done()


async def test_activate_writes_lamp_schedule(hass, lamp) -> None:
    server, state = lamp
    entry = await _add(hass, server, "SER1", _options(300))
    await entry.runtime_data.studio.activate_preset("a")
    await hass.async_block_till_done()
    assert state["schedule"]["name"] == "Lvl"
    assert len(state["schedule"]["ramps"]) == 5
    for ramp in state["schedule"]["ramps"]:
        assert ramp["points"] == [
            {"time": 0, "intensity": 300},
            {"time": 1439, "intensity": 300},
        ]
    assert entry.options[CONF_LAMP_WRITE_PENDING] is False


async def test_original_captured_once_before_first_write(hass, lamp) -> None:
    server, state = lamp
    entry = await _add(hass, server, "SER1", _options(300))
    studio = entry.runtime_data.studio
    await studio.activate_preset("a")
    await hass.async_block_till_done()
    original = entry.options[CONF_PRESETS]["original"]
    assert original["name"] == "Original (Lampe)"
    assert original["readonly"] is True
    assert original["ramps"]["blue"] == [[t, i] for t, i in POINTS]
    assert entry.options[CONF_ORIGINAL_CAPTURED] is True
    # Lamp now holds the written preset; a second activation must keep the backup.
    await _with_b(hass, entry)
    await studio.activate_preset("b")
    await hass.async_block_till_done()
    assert entry.options[CONF_PRESETS]["original"]["ramps"]["blue"] == [[t, i] for t, i in POINTS]
    assert state["schedule"]["name"] == "B"


async def test_original_not_overwritten_if_present(hass, lamp) -> None:
    server, state = lamp
    options = _options(300)
    mine = {"name": "Mine", "ramps": {c: [[0, 1]] for c in CHANNELS}, "readonly": True}
    options[CONF_PRESETS]["original"] = mine
    entry = await _add(hass, server, "SER1", options)
    await entry.runtime_data.studio.activate_preset("a")
    await hass.async_block_till_done()
    assert entry.options[CONF_PRESETS]["original"] == mine
    assert entry.options[CONF_ORIGINAL_CAPTURED] is True
    assert state["schedule"]["name"] == "Lvl"


async def _poll(hass, entry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_offline_sets_pending_and_retries_after_poll(hass, lamp) -> None:
    server, state = lamp
    entry = await _add(hass, server, "SER1", _options(300))
    state["overrides"][POST] = UNREACHABLE
    with patch(MONOTONIC, return_value=1000.0):
        await entry.runtime_data.studio.activate_preset("a")
        await hass.async_block_till_done()
    assert entry.options[CONF_LAMP_WRITE_PENDING] is True
    assert state["schedule"]["name"] == "default"
    del state["overrides"][POST]
    posts = state["calls"].count(POST)
    # Throttled: no retry within RETRY_MIN after the failure.
    with patch(MONOTONIC, return_value=1000.0 + RETRY_MIN - 1):
        await _poll(hass, entry)
    assert state["calls"].count(POST) == posts
    with patch(MONOTONIC, return_value=1000.0 + RETRY_MIN):
        await _poll(hass, entry)
    assert state["schedule"]["name"] == "Lvl"
    assert entry.options[CONF_LAMP_WRITE_PENDING] is False


async def test_retry_backoff_is_exponential_and_capped(hass, lamp) -> None:
    server, state = lamp
    entry = await _add(hass, server, "SER1", _options(300))
    coordinator = entry.runtime_data
    state["overrides"][POST] = UNREACHABLE
    delays = []
    with patch(MONOTONIC, return_value=0.0):
        for _ in range(5):
            await coordinator.write_active_schedule()
            delays.append(coordinator._retry_delay)
    assert delays == [RETRY_MIN, 2 * RETRY_MIN, 4 * RETRY_MIN, RETRY_MAX, RETRY_MAX]
    del state["overrides"][POST]
    await coordinator.write_active_schedule()
    assert coordinator._retry_delay == 0.0
    assert entry.options[CONF_LAMP_WRITE_PENDING] is False


async def test_rejected_schedule_is_reported_not_retried(hass, lamp) -> None:
    server, state = lamp
    entry = await _add(hass, server, "SER1", _options(300))
    state["fail_schedule_write"] = True  # lamp answers response_code 1
    with pytest.raises(LibraryError) as err:
        await entry.runtime_data.studio.activate_preset("a")
    await hass.async_block_till_done()
    assert err.value.code == "lamp_rejected"
    assert entry.options[CONF_ACTIVE_PRESET] == "a"
    assert entry.options[CONF_LAMP_WRITE_PENDING] is False
    posts = state["calls"].count(POST)
    with patch(MONOTONIC, return_value=1e9):
        for _ in range(3):
            await _poll(hass, entry)
    assert state["calls"].count(POST) == posts


@pytest.mark.parametrize(
    "override",
    [
        (200, '{"name": "x", "ramps": [{"color": "blue"}], "response_code": 0}', {"Content-Type": "application/json"}),
        (200, '{"name": "x", "ramps": [5], "response_code": 0}', {"Content-Type": "application/json"}),
        (200, '{"response_code": 2}', {"Content-Type": "application/json"}),
    ],
)
async def test_bad_original_capture_is_rejected(hass, lamp, override) -> None:
    server, state = lamp
    entry = await _add(hass, server, "SER1", _options(300))
    state["overrides"][("GET", "/api/schedule")] = override
    with pytest.raises(LibraryError) as err:
        await entry.runtime_data.studio.activate_preset("a")
    await hass.async_block_till_done()
    assert err.value.code == "lamp_rejected"
    assert entry.options[CONF_LAMP_WRITE_PENDING] is False
    assert entry.options[CONF_ORIGINAL_CAPTURED] is False
    assert POST not in state["calls"]


async def test_follower_lamp_written_on_leader_activate(hass, lamp) -> None:
    server, state = lamp
    leader = await _add(hass, server, "LEADER01", _options(300, "Leader"))
    app2, state2 = make_fake_lamp()
    server2 = TestServer(app2)
    await server2.start_server()
    try:
        follower = await _add(hass, server2, "FOLLOW01", {**_options(900, "Own"), CONF_FOLLOW: leader.entry_id})
        await _with_b(hass, leader)
        await leader.runtime_data.studio.activate_preset("b")
        await hass.async_block_till_done()
        assert state["schedule"]["name"] == "B"
        assert state2["schedule"]["name"] == "B"
        assert follower.options[CONF_LAMP_WRITE_PENDING] is False
    finally:
        await server2.close()


async def test_capture_failure_still_marks_pending(hass, lamp) -> None:
    server, state = lamp
    entry = await _add(hass, server, "SER1", _options(300))
    state["overrides"][("GET", "/api/schedule")] = (500, "boom", {})
    await entry.runtime_data.studio.activate_preset("a")
    await hass.async_block_till_done()
    assert entry.options[CONF_LAMP_WRITE_PENDING] is True
    assert entry.options[CONF_ORIGINAL_CAPTURED] is False
    assert "original" not in entry.options[CONF_PRESETS]
    assert state["schedule"]["name"] == "default"
    assert ("POST", "/api/schedule") not in state["calls"]


async def test_preset_saved_during_first_capture_is_not_lost(hass, lamp) -> None:
    server, state = lamp
    entry = await _add(hass, server, "SER1", _options(300))
    studio = entry.runtime_data.studio
    state["delay"] = 0.3
    task = hass.async_create_task(studio.activate_preset("a"))
    await asyncio.sleep(0.1)  # write_active_schedule is now waiting on the slow lamp
    ramps = {c: [(0, 200), (720, 200)] for c in CHANNELS}
    await studio.save_preset(None, "Late", ramps, False, lamp_write=False)
    await task
    await hass.async_block_till_done()
    names = {p["name"] for p in entry.options[CONF_PRESETS].values()}
    assert {"Lvl", "Late", "Original (Lampe)"} <= names


@pytest.fixture
async def lamp2(lamp):
    app, state = make_fake_lamp()
    server = TestServer(app)
    await server.start_server()
    yield server, state
    await server.close()


async def test_follower_waits_for_unloaded_leader_then_writes_its_schedule(
    hass, lamp, lamp2
) -> None:
    server, state = lamp
    server2, state2 = lamp2
    leader = MockConfigEntry(
        domain=DOMAIN,
        unique_id="LEADER01",
        data={CONF_HOST: f"{server.host}:{server.port}"},
        options=_options(300, "Leader"),
        version=2,
    )
    follower = await _add(
        hass,
        server2,
        "FOLLOW01",
        {**_options(900, "Own"), CONF_FOLLOW: leader.entry_id, CONF_LAMP_WRITE_PENDING: True},
    )
    leader.add_to_hass(hass)  # after the domain setup: known but not loaded
    with patch(MONOTONIC, return_value=1e9):
        for _ in range(2):
            await _poll(hass, follower)
    await follower.runtime_data.write_active_schedule()
    assert POST not in state2["calls"]  # never the follower's own schedule
    assert follower.options[CONF_LAMP_WRITE_PENDING] is True
    await hass.config_entries.async_setup(leader.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert leader.state is ConfigEntryState.LOADED
    assert state2["calls"].count(POST) == 1
    assert state2["schedule"]["name"] == "Leader"
    assert follower.options[CONF_LAMP_WRITE_PENDING] is False


async def _following_pair(hass, lamp, lamp2):
    server, _ = lamp
    server2, state2 = lamp2
    leader = await _add(hass, server, "LEADER01", _options(300, "Leader"))
    follower = await _add(hass, server2, "FOLLOW01", _options(900, "Own"))
    await follower.runtime_data.studio.set_follow(leader.entry_id)
    await hass.async_block_till_done()
    assert state2["schedule"]["name"] == "Leader"
    return leader, follower


async def test_follower_lamp_written_when_leader_removed(hass, lamp, lamp2) -> None:
    _, state2 = lamp2
    leader, follower = await _following_pair(hass, lamp, lamp2)
    await hass.config_entries.async_remove(leader.entry_id)
    await hass.async_block_till_done()
    assert follower.options[CONF_FOLLOW] is None
    assert state2["schedule"]["name"] == "Own"
    assert follower.options[CONF_LAMP_WRITE_PENDING] is False


async def test_follower_lamp_written_when_follow_cleared(hass, lamp, lamp2) -> None:
    _, state2 = lamp2
    _, follower = await _following_pair(hass, lamp, lamp2)
    await follower.runtime_data.studio.set_follow(None)
    await hass.async_block_till_done()
    assert state2["schedule"]["name"] == "Own"
    assert follower.options[CONF_LAMP_WRITE_PENDING] is False
