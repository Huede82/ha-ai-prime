"""Studio store, entry migration v2 and follow resolution."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, STATE_ON
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.ai_prime.api import Health
from custom_components.ai_prime.const import (
    CHANNELS,
    CONF_ACTIVE_PRESET,
    CONF_FOLLOW,
    CONF_LAMP_WRITE_PENDING,
    CONF_PRESET,
    CONF_PRESETS,
    CONF_SUN_LOCATION,
    DOMAIN,
)
from custom_components.ai_prime.library import LibraryError
from custom_components.ai_prime.nature.weather import WeatherSource
from custom_components.ai_prime.studio import effective

from .conftest import P, setup_entry

FLAT = {"name": "flat", "ramps": {c: [[0, 400], [720, 400]] for c in CHANNELS}}


def _entry_options(level: int, name: str = "Lvl") -> dict:
    ramps = {c: [[0, level], [720, level]] for c in CHANNELS}
    return {
        CONF_PRESETS: {"a": {"name": name, "ramps": ramps, "readonly": False}},
        CONF_ACTIVE_PRESET: "a",
        CONF_FOLLOW: None,
    }


async def _add(hass, stack: ExitStack, serial: str, options: dict) -> MockConfigEntry:
    """Set up one more entry on top of the shared client patches."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=serial,
        data={CONF_HOST: f"192.168.2.{len(serial)}{serial[-1]}"},
        options=options,
        version=2,
    )
    entry.add_to_hass(hass)
    with patch(
        f"{P}.identity",
        AsyncMock(return_value={"serial_number": serial, "product": "Prime"}),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _pair(hass, stack):
    leader, _, _ = await setup_entry(hass, stack, options=_entry_options(300, "Leader"))
    follower = await _add(hass, stack, "FOLLOWER0001", _entry_options(900, "Own"))
    return leader, follower


def _level(runner) -> int:
    return runner._preset.value_at(0)["blue"]


async def test_migration_v1_to_v2(hass, stop_patches) -> None:
    # Reuse the client patches of setup_entry, then set up an explicit v1 entry.
    await setup_entry(hass, stop_patches, options=_entry_options(100))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="OTHER000001",
        data={CONF_HOST: "192.168.2.51"},
        options={CONF_PRESET: FLAT},
        version=1,
    )
    entry.add_to_hass(hass)
    with patch(
        f"{P}.identity",
        AsyncMock(return_value={"serial_number": "OTHER000001", "product": "Prime"}),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.version == 2
    assert CONF_PRESET not in entry.options
    assert entry.options[CONF_ACTIVE_PRESET] == "natur"
    assert entry.options[CONF_PRESETS]["natur"]["name"] == "flat"
    assert entry.options[CONF_FOLLOW] is None


async def test_activate_switches_runner_preset(hass, stop_patches) -> None:
    options = _entry_options(300)
    options[CONF_PRESETS]["b"] = {
        "name": "B",
        "ramps": {c: [[0, 700], [720, 700]] for c in CHANNELS},
        "readonly": False,
    }
    entry, _, _ = await setup_entry(hass, stop_patches, options=options)
    runner = entry.runtime_data.nature
    assert _level(runner) == 300
    with patch.object(
        type(entry.runtime_data), "write_active_schedule", AsyncMock(return_value=True)
    ) as write:
        await entry.runtime_data.studio.activate_preset("b")
    assert entry.options[CONF_ACTIVE_PRESET] == "b"
    assert _level(runner) == 700
    write.assert_awaited_once()


async def test_follower_uses_leader_preset_and_locations(hass, stop_patches) -> None:
    leader, follower = await _pair(hass, stop_patches)
    assert _level(follower.runtime_data.nature) == 900
    await follower.runtime_data.studio.set_follow(leader.entry_id)
    assert effective(hass, follower)[CONF_PRESET]["name"] == "Leader"
    assert _level(follower.runtime_data.nature) == 300
    await leader.runtime_data.studio.set_locations(
        {CONF_SUN_LOCATION: {"latitude": 10.0, "longitude": 20.0}}
    )
    loc = follower.runtime_data.nature._location(CONF_SUN_LOCATION)
    assert (loc.latitude, loc.longitude) == (10.0, 20.0)


async def test_leader_change_refreshes_follower(hass, stop_patches) -> None:
    leader, follower = await _pair(hass, stop_patches)
    await follower.runtime_data.studio.set_follow(leader.entry_id)
    ramps = {c: [(0, 550), (720, 550)] for c in CHANNELS}
    await leader.runtime_data.studio.save_preset("a", "Leader", ramps, False)
    assert _level(follower.runtime_data.nature) == 550


async def test_leader_removed_follower_falls_back_and_follow_cleared(
    hass, stop_patches
) -> None:
    leader, follower = await _pair(hass, stop_patches)
    await follower.runtime_data.studio.set_follow(leader.entry_id)
    assert _level(follower.runtime_data.nature) == 300
    with patch(f"{P}.set_schedule", AsyncMock()):
        await hass.config_entries.async_remove(leader.entry_id)
        await hass.async_block_till_done()
    assert follower.options[CONF_FOLLOW] is None
    assert _level(follower.runtime_data.nature) == 900


async def test_leader_unloaded_follower_falls_back(hass, stop_patches) -> None:
    leader, follower = await _pair(hass, stop_patches)
    await follower.runtime_data.studio.set_follow(leader.entry_id)
    assert await hass.config_entries.async_unload(leader.entry_id)
    await hass.async_block_till_done()
    assert follower.options[CONF_FOLLOW] == leader.entry_id
    assert _level(follower.runtime_data.nature) == 900


async def test_set_follow_cycle_rejected(hass, stop_patches) -> None:
    leader, follower = await _pair(hass, stop_patches)
    await follower.runtime_data.studio.set_follow(leader.entry_id)
    with pytest.raises(LibraryError) as err:
        await leader.runtime_data.studio.set_follow(follower.entry_id)
    assert err.value.code == "follow_cycle"
    with pytest.raises(LibraryError):
        await leader.runtime_data.studio.set_follow(leader.entry_id)
    with pytest.raises(LibraryError) as err:
        await leader.runtime_data.studio.set_follow("nope")
    assert err.value.code == "not_found"


async def test_empty_name_rejected(hass, stop_patches) -> None:
    entry, _, _ = await setup_entry(hass, stop_patches, options=_entry_options(300))
    studio = entry.runtime_data.studio
    with pytest.raises(LibraryError) as err:
        await studio.save_preset(None, "   ", {c: [(0, 0)] for c in CHANNELS}, False)
    assert err.value.code == "invalid_name"
    with pytest.raises(LibraryError) as err:
        await studio.rename_preset("a", "")
    assert err.value.code == "invalid_name"


async def test_lamp_write_only_for_active_changes(hass, stop_patches) -> None:
    entry, _, _ = await setup_entry(hass, stop_patches, options=_entry_options(300))
    studio = entry.runtime_data.studio
    ramps = {c: [(0, 100), (720, 100)] for c in CHANNELS}
    with patch.object(
        type(entry.runtime_data), "write_active_schedule", AsyncMock(return_value=True)
    ) as write:
        new_id = await studio.save_preset(None, "Other", ramps, False)
        await studio.rename_preset(new_id, "Other2")
        await studio.delete_preset(new_id)
        assert write.await_count == 0
        await studio.save_preset("a", "Lvl", ramps, False)  # active preset edited
        assert write.await_count == 1


async def test_follower_set_up_before_leader_picks_up_leader(hass, stop_patches) -> None:
    anchor, _, _ = await setup_entry(hass, stop_patches, options=_entry_options(100))
    leader = MockConfigEntry(
        domain=DOMAIN,
        unique_id="LEADER000001",
        data={CONF_HOST: "192.168.2.61"},
        options={
            **_entry_options(300, "Leader"),
            CONF_SUN_LOCATION: {"latitude": 10.0, "longitude": 20.0},
        },
        version=2,
    )
    leader.add_to_hass(hass)
    follower_options = {**_entry_options(900, "Own"), CONF_FOLLOW: leader.entry_id}
    follower = await _add(hass, stop_patches, "FOLLOWER0002", follower_options)
    assert _level(follower.runtime_data.nature) == 900  # leader not loaded yet
    with patch(
        f"{P}.identity",
        AsyncMock(return_value={"serial_number": "LEADER000001", "product": "Prime"}),
    ):
        await hass.config_entries.async_setup(leader.entry_id)
        await hass.async_block_till_done()
    assert leader.state is ConfigEntryState.LOADED
    assert _level(follower.runtime_data.nature) == 300
    loc = follower.runtime_data.nature._location(CONF_SUN_LOCATION)
    assert (loc.latitude, loc.longitude) == (10.0, 20.0)


async def test_live_upgrade_v1_nature_on_does_not_write_lamp(hass, lamp) -> None:
    """v1 entry with nature mode restored ON: runner uses ``natur``, lamp schedule untouched."""
    server, state = lamp
    mock_restore_cache(hass, [State("switch.mock_title_nature_mode", STATE_ON)])
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="UPGRADE00001",
        data={CONF_HOST: f"{server.host}:{server.port}"},
        options={CONF_PRESET: FLAT},
        version=1,
    )
    entry.add_to_hass(hass)
    with patch.object(WeatherSource, "get", AsyncMock(return_value=None)):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.version == 2
        runner = entry.runtime_data.nature
        assert runner.active
        assert entry.options[CONF_ACTIVE_PRESET] == "natur"
        assert runner.preset.name == "flat"
        assert _level(runner) == 400
        for _ in range(4):
            await entry.runtime_data.async_refresh()
            await runner.tick(dt_util.now())
            await hass.async_block_till_done()
        await runner.stop()
    assert ("POST", "/api/schedule") not in state["calls"]
    assert ("POST", "/api/colors") in state["calls"]  # the runner did drive the lamp
    assert entry.options[CONF_LAMP_WRITE_PENDING] is False
