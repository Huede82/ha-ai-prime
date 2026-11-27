"""Live demo on the lamp (client patched)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.ai_prime.api import PrimeConnectionError
from custom_components.ai_prime.const import CHANNELS

from .conftest import P, get_eid, setup_entry
from .test_nature_runner import NOW, _mix, _nature_on, _setup

PCT = {c: 40.0 for c in CHANNELS}


async def _call(client, type_: str, **kwargs) -> dict:
    await client.send_json_auto_id({"type": f"ai_prime/{type_}", **kwargs})
    return await client.receive_json()


async def test_start_pauses_runner_without_touching_switch(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches)
    await _nature_on(hass)
    demo = entry.runtime_data.demo
    options = dict(entry.options)
    await demo.start()
    assert demo.active and runner.paused and runner.active
    assert hass.states.get(get_eid(hass, "switch", "nature_mode")).state == "on"
    assert entry.options == options
    n = len(m.mock_calls)
    await runner.tick(NOW)
    assert len(m.mock_calls) == n  # paused: no writes


async def test_start_disables_schedule(hass, stop_patches) -> None:
    entry, m, state = await setup_entry(hass, stop_patches, schedule=True)
    await entry.runtime_data.demo.start()
    assert state["schedule"] is False


async def test_frame_sends_clamped_colors(hass, stop_patches) -> None:
    entry, m, state = await setup_entry(hass, stop_patches)
    demo = entry.runtime_data.demo
    assert await demo.frame({**{c: 500 for c in CHANNELS}, "blue": 5000, "green": -5})
    assert demo.active  # implicit start
    assert state["colors"] == {**{c: 500 for c in CHANNELS}, "blue": 1000, "green": 0}


async def test_frame_dropped_while_in_flight(hass, stop_patches) -> None:
    import asyncio

    entry, m, state = await setup_entry(hass, stop_patches)
    demo = entry.runtime_data.demo
    gate = asyncio.Event()

    async def slow(values):
        await gate.wait()
        state["colors"] = dict(values)

    with patch(f"{P}.set_colors", side_effect=slow):
        first = hass.async_create_task(demo.frame({c: 100 for c in CHANNELS}))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert await demo.frame({c: 200 for c in CHANNELS}) is False
        gate.set()
        assert await first is True
    assert state["colors"] == {c: 100 for c in CHANNELS}


async def test_stop_resumes_runner(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches)
    await _nature_on(hass)
    demo = entry.runtime_data.demo
    await demo.frame({c: 900 for c in CHANNELS})
    assert state["colors"] == _mix(900)
    await demo.stop()
    assert not demo.active and not runner.paused and runner.active
    assert state["colors"] == _mix(300)  # engine value re-applied immediately
    assert hass.states.get(get_eid(hass, "switch", "nature_mode")).state == "on"
    await demo.stop()  # idempotent


async def test_stop_reenables_schedule(hass, stop_patches) -> None:
    entry, m, state = await setup_entry(hass, stop_patches, schedule=True)
    demo = entry.runtime_data.demo
    await demo.frame({c: 900 for c in CHANNELS})
    assert state["schedule"] is False
    await demo.stop()
    assert state["schedule"] is True
    assert not demo.active


async def test_stop_restores_colors(hass, stop_patches) -> None:
    colors = {"blue": 100, "green": 200, "deep_red": 0, "warm_white": 300, "cool_white": 400}
    entry, m, state = await setup_entry(hass, stop_patches, colors=colors)
    demo = entry.runtime_data.demo
    await demo.frame({c: 900 for c in CHANNELS})
    await demo.stop()
    assert state["colors"] == colors
    assert state["schedule"] is False


async def test_watchdog_stops_after_five_seconds(hass, stop_patches, freezer) -> None:
    colors = {c: 123 for c in CHANNELS}
    entry, m, state = await setup_entry(hass, stop_patches, colors=colors)
    demo = entry.runtime_data.demo
    await demo.frame({c: 900 for c in CHANNELS})
    freezer.tick(timedelta(seconds=3))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert demo.active
    await demo.frame({c: 800 for c in CHANNELS})  # resets the watchdog
    freezer.tick(timedelta(seconds=4))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert demo.active
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert not demo.active
    assert state["colors"] == colors


async def test_manual_change_stops_demo(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches)
    await _nature_on(hass)
    demo = entry.runtime_data.demo
    await demo.frame({c: 900 for c in CHANNELS})
    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: get_eid(hass, "number", "blue"), "value": 10},
        blocking=True,
    )
    assert not demo.active and not runner.paused
    assert hass.states.get(get_eid(hass, "switch", "nature_mode")).state == "off"
    assert state["colors"]["blue"] == 100


async def test_nature_switch_off_during_demo(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches)
    await _nature_on(hass)
    demo = entry.runtime_data.demo
    await demo.frame({c: 900 for c in CHANNELS})
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: get_eid(hass, "switch", "nature_mode")},
        blocking=True,
    )
    assert not demo.active and not runner.active and not runner.paused


async def test_unload_stops_demo(hass, stop_patches) -> None:
    colors = {c: 50 for c in CHANNELS}
    entry, m, state = await setup_entry(hass, stop_patches, colors=colors)
    await entry.runtime_data.demo.frame({c: 900 for c in CHANNELS})
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert state["colors"] == colors


# -- websocket -------------------------------------------------------------
async def test_ws_frame_and_stop(hass, stop_patches, hass_ws_client) -> None:
    entry, m, state = await setup_entry(hass, stop_patches, schedule=True)
    client = await hass_ws_client(hass)
    res = await _call(client, "demo/frame", entry_id=entry.entry_id,
                      values={**PCT, "blue": 12.34})
    assert res["success"]
    assert state["colors"] == {**{c: 400 for c in CHANNELS}, "blue": 123}
    assert (await _call(client, "demo/stop", entry_id=entry.entry_id))["success"]
    assert state["schedule"] is True


@pytest.mark.parametrize(
    "values",
    [{**PCT, "blue": 101}, {**PCT, "blue": -1}, {"blue": 10}, {**PCT, "x": 1}, "nope"],
)
async def test_ws_frame_validation(hass, stop_patches, hass_ws_client, values) -> None:
    entry, m, state = await setup_entry(hass, stop_patches)
    client = await hass_ws_client(hass)
    res = await _call(client, "demo/frame", entry_id=entry.entry_id, values=values)
    assert not res["success"]
    assert res["error"]["code"] == "invalid_format"
    assert not entry.runtime_data.demo.active


async def test_ws_unknown_entry(hass, stop_patches, hass_ws_client) -> None:
    await setup_entry(hass, stop_patches)
    client = await hass_ws_client(hass)
    res = await _call(client, "demo/frame", entry_id="nope", values=PCT)
    assert res["error"]["code"] == "not_found"


async def test_ws_lamp_unreachable(hass, stop_patches, hass_ws_client) -> None:
    entry, m, state = await setup_entry(hass, stop_patches)
    client = await hass_ws_client(hass)
    with patch(f"{P}.set_colors", side_effect=PrimeConnectionError("down")):
        res = await _call(client, "demo/frame", entry_id=entry.entry_id, values=PCT)
    assert res["error"]["code"] == "lamp_unreachable"


async def test_ws_close_stops_demo(hass, stop_patches, hass_ws_client) -> None:
    colors = {c: 77 for c in CHANNELS}
    entry, m, state = await setup_entry(hass, stop_patches, colors=colors)
    client = await hass_ws_client(hass)
    await _call(client, "demo/frame", entry_id=entry.entry_id, values=PCT)
    assert entry.runtime_data.demo.active
    await client.close()
    await hass.async_block_till_done()
    assert not entry.runtime_data.demo.active
    assert state["colors"] == colors
