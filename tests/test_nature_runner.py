"""Nature mode runner and entity tests (client and weather patched)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from contextlib import ExitStack
from unittest.mock import AsyncMock, PropertyMock, patch

import aiohttp
import pytest
from dataclasses import replace
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import CoreState, State
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.ai_prime.api import PrimeApiError
from custom_components.ai_prime.const import CHANNELS, CONF_CLOUD_STRENGTH, CONF_PRESET
from custom_components.ai_prime.nature.weather import Weather, WeatherSource

from .conftest import P, get_eid, setup_entry

RUNNER = "custom_components.ai_prime.nature.runner"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
FLAT = {
    "name": "flat",
    "ramps": {c: [[0, 400], [720, 400]] for c in CHANNELS},
}
OPTIONS = {CONF_PRESET: FLAT, CONF_CLOUD_STRENGTH: 50}


def _mix(v: int) -> dict[str, int]:
    return {c: v for c in CHANNELS}


def _weather(cloud: float = 0.5, storm: bool = False):
    stack = ExitStack()
    stack.enter_context(
        patch.object(WeatherSource, "get", AsyncMock(return_value=Weather(cloud, storm, NOW)))
    )
    stack.enter_context(
        patch.object(WeatherSource, "available", PropertyMock(return_value=True))
    )
    return stack


async def _setup(hass, stack, cloud=0.5, storm=False, options=None, **kw):
    stack.enter_context(_weather(cloud, storm))
    entry, m, state = await setup_entry(
        hass, stack, options=options or dict(OPTIONS), **kw
    )
    return entry, m, state, entry.runtime_data.nature


async def _call(hass, domain, service, eid, **data):
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: eid, **data}, blocking=True
    )


async def _nature_on(hass):
    await _call(hass, "switch", "turn_on", get_eid(hass, "switch", "nature_mode"))
    await hass.async_block_till_done()


async def test_nature_on_disables_schedule_and_writes_target(
    hass, stop_patches, freezer
) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches, schedule=True)
    await _nature_on(hass)
    assert runner.active
    assert m.mock_calls[0] == ("set_schedule", (False,), {})
    assert m.mock_calls[1] == ("set_colors", (_mix(300),), {})  # 400 * (1 - 0.5*0.5)
    assert state["schedule"] is False
    assert hass.states.get(get_eid(hass, "switch", "nature_mode")).state == STATE_ON
    st = hass.states.get(get_eid(hass, "sensor", "nature_target"))
    assert float(st.state) == 30.0
    assert st.attributes["blue"] == 300
    assert st.attributes["polar"] is False
    assert float(hass.states.get(get_eid(hass, "sensor", "cloud_cover")).state) == 50
    assert 0 <= float(hass.states.get(get_eid(hass, "sensor", "moon_phase")).state) <= 100


async def test_tick_writes_only_on_change(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, _, runner = await _setup(hass, stop_patches)
    await _nature_on(hass)
    await runner.tick(NOW)
    assert len(m.set_colors.mock_calls) == 1
    WeatherSource.get.return_value = Weather(0.0, False, NOW)
    await runner.tick(NOW)
    assert m.set_colors.mock_calls[-1].args[0] == _mix(400)
    assert len(m.set_colors.mock_calls) == 2


async def test_manual_number_turns_nature_off(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, _, runner = await _setup(hass, stop_patches)
    await _nature_on(hass)
    await _call(hass, "number", "set_value", get_eid(hass, "number", "blue"), value=10)
    assert not runner.active
    assert hass.states.get(get_eid(hass, "switch", "nature_mode")).state == "off"
    n = len(m.mock_calls)
    WeatherSource.get.return_value = Weather(0.0, False, NOW)
    await runner.tick(NOW)
    assert len(m.mock_calls) == n
    assert m.set_colors.mock_calls[-1].args[0]["blue"] == 100


async def test_schedule_switch_on_turns_nature_off(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, _, runner = await _setup(hass, stop_patches)
    await _nature_on(hass)
    await _call(hass, "switch", "turn_on", get_eid(hass, "switch", "internal_schedule"))
    assert not runner.active
    assert m.set_schedule.mock_calls[-1].args == (True,)


async def test_restore_nature_mode_after_restart(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    mock_restore_cache(
        hass,
        [
            State("switch.mock_title_nature_mode", STATE_ON),
            State("switch.mock_title_lightning", STATE_ON),
        ],
    )
    entry, m, state, runner = await _setup(hass, stop_patches, schedule=True)
    await hass.async_block_till_done()
    assert runner.active
    assert runner.lightning_enabled
    assert state["schedule"] is False
    assert state["colors"] == _mix(300)


async def test_weather_down_runner_continues(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    with patch.object(
        WeatherSource, "_fetch", AsyncMock(side_effect=aiohttp.ClientError("down"))
    ):
        entry, m, _ = await setup_entry(hass, stop_patches, options=dict(OPTIONS))
        runner = entry.runtime_data.nature
        await _nature_on(hass)
        await runner.tick(NOW)
    assert runner.active
    assert m.set_colors.mock_calls[-1].args[0] == _mix(400)  # clouds 0 without data


async def test_three_lamp_errors_mark_unavailable_then_recover(
    hass, stop_patches, freezer
) -> None:
    freezer.move_to(NOW)
    entry, m, _, runner = await _setup(hass, stop_patches)
    await _nature_on(hass)
    eid = get_eid(hass, "sensor", "nature_target")
    with patch(f"{P}.set_colors", AsyncMock(side_effect=PrimeApiError("boom"))):
        WeatherSource.get.return_value = Weather(0.0, False, NOW)
        for _ in range(2):
            await runner.tick(NOW)
        assert hass.states.get(eid).state != STATE_UNAVAILABLE
        await runner.tick(NOW)
        assert hass.states.get(eid).state == STATE_UNAVAILABLE
    await runner.tick(NOW)
    assert float(hass.states.get(eid).state) == 40.0
    assert runner.active


async def test_lightning_only_with_thunderstorm_and_switch(
    hass, stop_patches, freezer
) -> None:
    freezer.move_to(NOW)
    entry, m, _, runner = await _setup(hass, stop_patches, storm=True)
    await _nature_on(hass)
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    with patch(f"{RUNNER}._sleep", fake_sleep):
        before = len(m.set_colors.mock_calls)
        await runner._lightning_step()  # switch off -> idle
        assert len(m.set_colors.mock_calls) == before
        await _call(hass, "switch", "turn_on", get_eid(hass, "switch", "lightning"))
        await runner._lightning_step()
        posts = [c.args[0] for c in m.set_colors.mock_calls[before:]]
        assert any(p["cool_white"] == 1000 and p["blue"] == 1000 for p in posts)
        assert posts[-1] == _mix(300)
        # no thunderstorm -> no flashes
        WeatherSource.get.return_value = Weather(0.5, False, NOW)
        await runner.tick(NOW)
        n = len(m.set_colors.mock_calls)
        await runner._lightning_step()
        assert len(m.set_colors.mock_calls) == n


async def test_reload_preset_button_keeps_other_options(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, _, runner = await _setup(
        hass, stop_patches, options={**OPTIONS, CONF_CLOUD_STRENGTH: 70}
    )
    await _nature_on(hass)
    coordinator = entry.runtime_data
    payload = {
        "name": "lamp",
        "ramps": [
            {"color": "blue", "points": [{"time": 0, "intensity": 0}, {"time": 600, "intensity": 800}]}
        ],
    }
    schedule_calls = len(m.set_schedule.mock_calls)
    with patch(f"{P}.schedule", AsyncMock(return_value=payload)):
        await _call(hass, "button", "press", get_eid(hass, "button", "reload_preset"))
        await hass.async_block_till_done()
    assert entry.options[CONF_CLOUD_STRENGTH] == 70
    stored = entry.options["presets"][entry.options["active_preset"]]
    assert stored["name"] == "flat"
    assert [list(p) for p in stored["ramps"]["blue"]] == [[0, 0], [600, 800]]
    assert entry.runtime_data is coordinator  # not reloaded
    assert runner.active
    assert len(m.set_schedule.mock_calls) == schedule_calls


async def test_write_lock_serialises_light_and_number(hass, stop_patches) -> None:
    colors = {"deep_red": 0, "cool_white": 0, "green": 250, "blue": 500, "warm_white": 0}
    entry, m, state, _ = await _setup(hass, stop_patches, colors=colors)
    sent: list[dict[str, int]] = []

    async def slow_set_colors(values):
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        sent.append(dict(values))
        state["colors"] = dict(values)

    with patch(f"{P}.set_colors", side_effect=slow_set_colors):
        await asyncio.gather(
            _call(hass, "light", "turn_on", get_eid(hass, "light", "light"), brightness=51),
            _call(hass, "number", "set_value", get_eid(hass, "number", "blue"), value=30),
        )
    assert len(sent) == 2
    assert sent[-1]["blue"] == 300  # number's change
    assert sent[-1]["green"] == 100  # light's scaling (250 * 200/500)


async def test_stop_during_first_tick_registers_nothing(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, _, runner = await _setup(hass, stop_patches)
    gate = asyncio.Event()

    async def slow_get(_self, now):
        await gate.wait()
        return Weather(0.5, False, now)

    with patch.object(WeatherSource, "get", slow_get), patch(
        f"{RUNNER}.async_track_time_interval"
    ) as track:
        starter = asyncio.create_task(runner.start())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        stopper = asyncio.create_task(runner.stop())
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(starter, stopper)
        await hass.async_block_till_done()
    assert not runner.active
    assert runner._unsub_timer is None
    assert runner._lightning_task is None


async def test_concurrent_starts_register_once(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, _, runner = await _setup(hass, stop_patches)
    with patch(f"{RUNNER}.async_track_time_interval") as track:
        await asyncio.gather(runner.start(), runner.start())
    assert track.call_count == 1
    assert len(m.set_schedule.mock_calls) == 1
    assert runner._lightning_task is not None
    await runner.stop()


async def _flash_blocked_runner(hass, stop_patches, freezer):
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches, storm=True)
    real_sleep = asyncio.sleep
    started = asyncio.Event()

    async def fake_sleep(s: float) -> None:
        if s == 0.15:  # flash on-duration: hang here
            started.set()
            await asyncio.Event().wait()
        await real_sleep(0)

    runner.lightning_enabled = True
    return entry, m, state, runner, fake_sleep, started


async def test_stop_mid_flash_restores_target(hass, stop_patches, freezer) -> None:
    entry, m, state, runner, fake_sleep, started = await _flash_blocked_runner(
        hass, stop_patches, freezer
    )
    with patch(f"{RUNNER}._sleep", fake_sleep):
        await _nature_on(hass)
        await asyncio.wait_for(started.wait(), 5)
        assert state["colors"]["blue"] == 1000
        await runner.stop()
    assert state["colors"] == _mix(300)
    assert m.set_colors.mock_calls[-1].args[0] == _mix(300)


async def test_failed_flash_restore_rewrites_on_next_tick(
    hass, stop_patches, freezer
) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches, storm=True)
    await _nature_on(hass)
    runner.lightning_enabled = True

    async def no_sleep(s: float) -> None:
        return None

    async def flaky(values):
        if values["blue"] != 1000:
            raise PrimeApiError("boom")
        state["colors"] = dict(values)

    with patch(f"{RUNNER}._sleep", no_sleep), patch(f"{P}.set_colors", side_effect=flaky):
        await runner._lightning_step()
    assert runner._last_written is None
    n = len(m.set_colors.mock_calls)
    await runner.tick(NOW)
    assert len(m.set_colors.mock_calls) == n + 1
    assert m.set_colors.mock_calls[-1].args[0] == _mix(300)


async def test_restore_cancelled_when_unloaded_before_started(
    hass, stop_patches, freezer
) -> None:
    freezer.move_to(NOW)
    mock_restore_cache(hass, [State("switch.mock_title_nature_mode", STATE_ON)])
    hass.set_state(CoreState.starting)
    entry, m, state, runner = await _setup(hass, stop_patches)
    assert not runner.active
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert not runner.active
    assert m.set_schedule.mock_calls == []


async def test_start_without_preset_refuses(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, _, runner = await _setup(
        hass, stop_patches, options={CONF_CLOUD_STRENGTH: 50}
    )
    await _nature_on(hass)
    assert not runner.active
    assert m.set_schedule.mock_calls == []
    assert hass.states.get(get_eid(hass, "switch", "nature_mode")).state == "off"


async def test_no_lightning_when_dark(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    dark = {"name": "dark", "ramps": {c: [[0, 0], [720, 0]] for c in CHANNELS}}
    with patch(f"{RUNNER}.moon_window", return_value=[]):
        entry, m, _, runner = await _setup(
            hass, stop_patches, storm=True, options={CONF_PRESET: dark}
        )
        await _nature_on(hass)
        runner.lightning_enabled = True

        async def no_sleep(s: float) -> None:
            return None

        n = len(m.set_colors.mock_calls)
        with patch(f"{RUNNER}._sleep", no_sleep):
            await runner._lightning_step()
    assert len(m.set_colors.mock_calls) == n


# -- final review fixes ---------------------------------------------------


async def test_restore_retries_until_start_succeeds(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    mock_restore_cache(hass, [State("switch.mock_title_nature_mode", STATE_ON)])
    hass.set_state(CoreState.starting)
    entry, m, state, runner = await _setup(hass, stop_patches)
    calls = []

    async def flaky(enabled):
        calls.append(enabled)
        if len(calls) == 1:
            raise PrimeApiError("down")
        state["schedule"] = enabled

    with patch(f"{P}.set_schedule", side_effect=flaky):
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
        assert not runner.active
        freezer.tick(61)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert runner.active
    assert calls == [False, False]
    await runner.stop()


async def test_restore_retry_cancelled_when_switch_turned_off(
    hass, stop_patches, freezer
) -> None:
    freezer.move_to(NOW)
    mock_restore_cache(hass, [State("switch.mock_title_nature_mode", STATE_ON)])
    hass.set_state(CoreState.starting)
    entry, m, state, runner = await _setup(hass, stop_patches)
    with patch(f"{P}.set_schedule", side_effect=PrimeApiError("down")) as sched:
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
        await _call(hass, "switch", "turn_off", get_eid(hass, "switch", "nature_mode"))
        freezer.tick(61)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert sched.call_count == 1
    assert not runner.active


async def test_restore_retry_not_rescheduled_when_turned_off_mid_attempt(
    hass, stop_patches, freezer
) -> None:
    freezer.move_to(NOW)
    mock_restore_cache(hass, [State("switch.mock_title_nature_mode", STATE_ON)])
    hass.set_state(CoreState.starting)
    entry, m, state, runner = await _setup(hass, stop_patches)
    gate = asyncio.Event()
    calls = []

    async def slow_fail(enabled):
        calls.append(enabled)
        await gate.wait()
        raise PrimeApiError("down")

    with patch(f"{P}.set_schedule", side_effect=slow_fail):
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert calls == [False]
        off = hass.async_create_task(
            _call(hass, "switch", "turn_off", get_eid(hass, "switch", "nature_mode"))
        )
        await asyncio.sleep(0)
        gate.set()
        await off
        await hass.async_block_till_done()
        freezer.tick(61)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert calls == [False]
    assert not runner.active


async def test_failed_first_tick_re_enables_schedule(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches, schedule=True)
    with patch.object(WeatherSource, "get", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(HomeAssistantError):
            await runner.start()
    assert not runner.active
    assert [c.args for c in m.set_schedule.mock_calls] == [(False,), (True,)]
    assert state["schedule"] is True


async def test_tick_rewrites_when_lamp_schedule_comes_back(
    hass, stop_patches, freezer
) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches)
    await _nature_on(hass)
    m.reset_mock()
    coord = runner.coordinator
    state["schedule"] = True  # lamp rebooted / app re-enabled its schedule
    coord.async_set_updated_data(replace(coord.data, schedule_enabled=True))
    await runner.tick(NOW)
    assert [c[0] for c in m.mock_calls] == ["set_schedule", "set_colors"]
    assert m.set_schedule.mock_calls[0].args == (False,)
    assert state["schedule"] is False
    m.reset_mock()
    await runner.tick(NOW)
    assert m.mock_calls == []


async def test_tick_rewrites_when_lamp_colors_differ(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches)
    await _nature_on(hass)
    m.reset_mock()
    coord = runner.coordinator
    coord.async_set_updated_data(replace(coord.data, colors=_mix(0)))
    await runner.tick(NOW)
    assert m.set_colors.mock_calls[-1].args[0] == _mix(300)


async def test_remove_entry_re_enables_schedule(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches)
    await hass.config_entries.async_remove(entry.entry_id)
    assert m.set_schedule.mock_calls[-1].args == (True,)


async def test_remove_entry_swallows_lamp_errors(hass, stop_patches, freezer, caplog) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches)
    with patch(f"{P}.set_schedule", side_effect=PrimeApiError("down")):
        await hass.config_entries.async_remove(entry.entry_id)
    assert "Could not re-enable lamp schedule on removal" in caplog.text


async def test_flash_restore_retries_failed_post(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches, storm=True)
    await _nature_on(hass)
    runner.lightning_enabled = True
    sleeps = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    restores = []

    async def flaky(values):
        if values["blue"] != 1000:
            restores.append(values)
            if len(restores) <= 2:
                raise PrimeApiError("boom")
        state["colors"] = dict(values)

    with patch(f"{RUNNER}._sleep", fake_sleep), patch(
        f"{P}.set_colors", side_effect=flaky
    ), patch.object(runner._scheduler, "flash_pattern", return_value=[(0.15, 0.1)]):
        await runner._lightning_step()
    assert len(restores) == 3
    assert runner._last_written == _mix(300)
    assert state["colors"] == _mix(300)
    assert sleeps.count(1) == 2


async def test_cancel_mid_flash_restores_target(hass, stop_patches, freezer) -> None:
    entry, m, state, runner, fake_sleep, started = await _flash_blocked_runner(
        hass, stop_patches, freezer
    )
    with patch(f"{RUNNER}._sleep", fake_sleep):
        await _nature_on(hass)
        await asyncio.wait_for(started.wait(), 5)
        assert state["colors"]["blue"] == 1000
        task = runner._lightning_task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert state["colors"] == _mix(300)
    assert not runner._flash_on
    await runner.stop()


async def test_lightning_loop_survives_iteration_error(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches)
    calls = 0

    async def step():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        if calls == 3:
            raise asyncio.CancelledError

    async def no_sleep(s: float) -> None:
        return None

    with patch.object(runner, "_lightning_step", step), patch(f"{RUNNER}._sleep", no_sleep):
        with pytest.raises(asyncio.CancelledError):
            await runner._lightning_loop()
    assert calls == 3


async def test_stop_after_crashed_lightning_task(hass, stop_patches, freezer) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches)
    await _nature_on(hass)

    async def crash():
        raise RuntimeError("boom")

    runner._lightning_task.cancel()
    await asyncio.gather(runner._lightning_task, return_exceptions=True)
    runner._lightning_task = asyncio.create_task(crash())
    await asyncio.sleep(0)
    await runner.stop()
    assert not runner.active


async def test_cloud_cover_unavailable_when_inactive_or_weather_stale(
    hass, stop_patches, freezer
) -> None:
    freezer.move_to(NOW)
    entry, m, state, runner = await _setup(hass, stop_patches)
    eid = get_eid(hass, "sensor", "cloud_cover")
    assert hass.states.get(eid).state == STATE_UNAVAILABLE  # runner inactive
    await _nature_on(hass)
    assert hass.states.get(eid).state == "50"
    with patch.object(WeatherSource, "available", PropertyMock(return_value=False)):
        await runner.tick(NOW)
        assert hass.states.get(eid).state == STATE_UNAVAILABLE
