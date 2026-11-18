"""Control entity tests (client methods patched, call order recorded)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.exceptions import HomeAssistantError

from custom_components.ai_prime.api import PrimeApiError
from custom_components.ai_prime.const import CHANNELS, CONF_PRESET
from custom_components.ai_prime.preset import Preset

from .conftest import P, get_eid, setup_entry

ZERO = {c: 0 for c in CHANNELS}
FULL = {c: 1000 for c in CHANNELS}


def _mix(**kw: int) -> dict[str, int]:
    return {**ZERO, **kw}


async def _setup(hass, stack, colors, schedule=False, options=None):
    entry, manager, state = await setup_entry(hass, stack, colors, schedule, options)
    return entry, manager, state, []


async def _call(hass, domain, service, entity_id, **data):
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )


async def test_number_set_disables_schedule_first(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, _mix(blue=100), schedule=True)
    await _call(hass, "number", "set_value", get_eid(hass, "number", "blue"), value=42.5)
    assert m.mock_calls[:2] == [
        ("set_schedule", (False,), {}),
        ("set_colors", (_mix(blue=425),), {}),
    ]
    assert len(m.mock_calls) == 2


async def test_number_set_schedule_already_off(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, _mix(blue=100), schedule=False)
    await _call(hass, "number", "set_value", get_eid(hass, "number", "blue"), value=42.5)
    assert m.mock_calls == [("set_colors", (_mix(blue=425),), {})]


async def test_number_state_is_percent(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, _mix(green=425))
    st = hass.states.get(get_eid(hass, "number", "green"))
    assert float(st.state) == 42.5
    assert st.attributes["unit_of_measurement"] == "%"


async def test_light_brightness_scales_channels(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, _mix(deep_red=400, cool_white=800))
    await _call(hass, "light", "turn_on", get_eid(hass, "light", "light"),
                **{ATTR_BRIGHTNESS: 255})
    assert m.mock_calls == [
        ("set_colors", (_mix(deep_red=500, cool_white=1000),), {})
    ]


async def test_light_brightness_state(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, _mix(deep_red=400, cool_white=1000))
    st = hass.states.get(get_eid(hass, "light", "light"))
    assert st.state == "on"
    assert st.attributes[ATTR_BRIGHTNESS] == 255
    assert st.attributes["supported_color_modes"] == ["brightness"]


async def test_light_off_sends_zero(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, _mix(blue=300), schedule=True)
    await _call(hass, "light", "turn_off", get_eid(hass, "light", "light"))
    assert m.mock_calls == [
        ("set_schedule", (False,), {}),
        ("set_colors", (ZERO,), {}),
    ]


async def test_light_on_restores_last_mix(hass, stop_patches) -> None:
    entry, m, state, ps = await _setup(hass, stop_patches, _mix(blue=300, cool_white=600))
    eid = get_eid(hass, "light", "light")
    await _call(hass, "light", "turn_off", eid)
    state["colors"] = dict(ZERO)
    await entry.runtime_data.async_refresh()
    m.reset_mock()
    await _call(hass, "light", "turn_on", eid)
    assert m.mock_calls == [("set_colors", (_mix(blue=300, cool_white=600),), {})]
    m.reset_mock()
    # Restored mix scaled to a target brightness.
    state["colors"] = dict(ZERO)
    await entry.runtime_data.async_refresh()
    await _call(hass, "light", "turn_on", eid, **{ATTR_BRIGHTNESS: 128})
    # target_max = round(128/255*1000) = 502; factor 502/600
    assert m.mock_calls == [
        ("set_colors", (_mix(blue=251, cool_white=502),), {})
    ]


async def test_light_last_mix_ignores_polled_changes(hass, stop_patches) -> None:
    entry, m, state, ps = await _setup(hass, stop_patches, ZERO)
    state["colors"] = _mix(green=200)  # changed outside HA
    await entry.runtime_data.async_refresh()
    state["colors"] = dict(ZERO)
    await entry.runtime_data.async_refresh()
    await _call(hass, "light", "turn_on", get_eid(hass, "light", "light"))
    assert m.mock_calls == [("set_colors", (FULL,), {})]


async def test_light_fade_out_mix_is_not_restored(hass, stop_patches) -> None:
    preset = Preset(name="p", ramps={"blue": ((0, 0), (720, 800), (1440, 0))})
    entry, m, state, ps = await _setup(
        hass, stop_patches, _mix(blue=2), options={CONF_PRESET: preset.to_dict()}
    )
    eid = get_eid(hass, "light", "light")
    await _call(hass, "light", "turn_off", eid)
    m.reset_mock()
    await _call(hass, "light", "turn_on", eid)
    assert m.mock_calls == [("set_colors", (_mix(blue=800),), {})]


async def test_light_fade_out_mix_falls_back_to_full(hass, stop_patches) -> None:
    entry, m, state, ps = await _setup(hass, stop_patches, _mix(blue=2))
    eid = get_eid(hass, "light", "light")
    await _call(hass, "light", "turn_off", eid)
    m.reset_mock()
    await _call(hass, "light", "turn_on", eid)
    assert m.mock_calls == [("set_colors", (FULL,), {})]


async def test_concurrent_number_writes_keep_both_channels(hass, stop_patches) -> None:
    entry, m, state, ps = await _setup(hass, stop_patches, ZERO)
    await asyncio.gather(
        _call(hass, "number", "set_value", get_eid(hass, "number", "blue"), value=10),
        _call(hass, "number", "set_value", get_eid(hass, "number", "green"), value=20),
    )
    assert state["colors"] == _mix(blue=100, green=200)


async def test_light_on_fallback_peak_then_full(hass, stop_patches) -> None:
    preset = Preset(
        name="p",
        ramps={"blue": ((0, 0), (720, 800), (1440, 0)), "green": ((0, 0), (720, 400), (1440, 0))},
    )
    entry, m, _, ps = await _setup(hass, stop_patches, ZERO, options={CONF_PRESET: preset.to_dict()})
    await _call(hass, "light", "turn_on", get_eid(hass, "light", "light"))
    assert m.mock_calls == [("set_colors", (_mix(blue=800, green=400),), {})]


async def test_light_on_fallback_full_without_preset(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, ZERO)
    await _call(hass, "light", "turn_on", get_eid(hass, "light", "light"))
    assert m.mock_calls == [("set_colors", (FULL,), {})]


async def test_light_on_fallback_full_with_all_zero_preset(hass, stop_patches) -> None:
    preset = Preset(name="p", ramps={"blue": ((0, 0), (720, 0))})
    entry, m, _, ps = await _setup(hass, stop_patches, ZERO, options={CONF_PRESET: preset.to_dict()})
    await _call(hass, "light", "turn_on", get_eid(hass, "light", "light"))
    assert m.mock_calls == [("set_colors", (FULL,), {})]


async def test_api_error_raises_homeassistant_error(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, _mix(blue=100))
    with patch(f"{P}.set_colors", AsyncMock(side_effect=PrimeApiError(3))):
        with pytest.raises(HomeAssistantError):
            await _call(hass, "number", "set_value", get_eid(hass, "number", "blue"), value=10)
        with pytest.raises(HomeAssistantError):
            await _call(hass, "light", "turn_off", get_eid(hass, "light", "light"))


async def test_switch_turns_schedule_on_and_off(hass, stop_patches) -> None:
    entry, m, state, ps = await _setup(hass, stop_patches, _mix(blue=100), schedule=False)
    eid = get_eid(hass, "switch", "internal_schedule")
    assert hass.states.get(eid).state == "off"
    await _call(hass, "switch", "turn_on", eid)
    await _call(hass, "switch", "turn_off", eid)
    assert m.mock_calls == [("set_schedule", (True,), {}), ("set_schedule", (False,), {})]


async def test_switch_error_raises_homeassistant_error(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, _mix(blue=100))
    with patch(f"{P}.set_schedule", AsyncMock(side_effect=PrimeApiError(3))):
        with pytest.raises(HomeAssistantError):
            await _call(hass, "switch", "turn_on", get_eid(hass, "switch", "internal_schedule"))


async def test_consecutive_number_writes_accumulate(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, _mix(cool_white=100), schedule=True)
    await _call(hass, "number", "set_value", get_eid(hass, "number", "blue"), value=10)
    await _call(hass, "number", "set_value", get_eid(hass, "number", "green"), value=20)
    await _call(hass, "number", "set_value", get_eid(hass, "number", "deep_red"), value=30)
    assert m.mock_calls[-1] == (
        "set_colors",
        (_mix(cool_white=100, blue=100, green=200, deep_red=300),),
        {},
    )
    # Schedule disabled only once.
    assert [c for c in m.mock_calls if c[0] == "set_schedule"] == [("set_schedule", (False,), {})]
    assert float(hass.states.get(get_eid(hass, "number", "blue")).state) == 10


async def test_plain_turn_on_when_already_on_is_noop(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, _mix(blue=300), schedule=True)
    await _call(hass, "light", "turn_on", get_eid(hass, "light", "light"))
    assert m.mock_calls == []


async def test_light_brightness_with_schedule_on(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, _mix(blue=500), schedule=True)
    await _call(hass, "light", "turn_on", get_eid(hass, "light", "light"),
                **{ATTR_BRIGHTNESS: 255})
    assert m.mock_calls == [
        ("set_schedule", (False,), {}),
        ("set_colors", (_mix(blue=1000),), {}),
    ]


async def test_partial_failure_updates_schedule_state(hass, stop_patches) -> None:
    entry, m, _, ps = await _setup(hass, stop_patches, _mix(blue=500), schedule=True)
    with patch(f"{P}.set_colors", AsyncMock(side_effect=PrimeApiError(3))):
        with pytest.raises(HomeAssistantError):
            await _call(hass, "number", "set_value", get_eid(hass, "number", "blue"), value=10)
    assert entry.runtime_data.data.schedule_enabled is False
