"""Health sensor tests (client methods patched)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er

from custom_components.ai_prime.api import Health, PrimeConnectionError

from .conftest import P, get_eid, setup_entry


async def _setup(hass, stack):
    entry, _, _ = await setup_entry(hass, stack, health=Health(45.6037, True, False))
    return entry


async def test_temperature_state(hass, stop_patches) -> None:
    await _setup(hass, stop_patches)
    st = hass.states.get(get_eid(hass, "sensor", "temperature"))
    assert round(float(st.state), 1) == 45.6
    assert st.attributes["unit_of_measurement"] == "°C"
    assert st.attributes["device_class"] == "temperature"
    assert st.attributes["state_class"] == "measurement"
    entity = er.async_get(hass).async_get(st.entity_id)
    assert entity.entity_category is None


async def test_fan_on(hass, stop_patches) -> None:
    await _setup(hass, stop_patches)
    eid = get_eid(hass, "binary_sensor", "fan")
    st = hass.states.get(eid)
    assert st.state == "on"
    assert st.attributes["device_class"] == "running"
    assert er.async_get(hass).async_get(eid).entity_category == "diagnostic"


async def test_thermal_shutdown_off(hass, stop_patches) -> None:
    await _setup(hass, stop_patches)
    eid = get_eid(hass, "binary_sensor", "thermal_shutdown")
    st = hass.states.get(eid)
    assert st.state == "off"
    assert st.attributes["device_class"] == "problem"
    assert er.async_get(hass).async_get(eid).entity_category == "diagnostic"


async def test_unavailable_when_offline(hass, stop_patches) -> None:
    entry = await _setup(hass, stop_patches)
    ids = [
        get_eid(hass, "sensor", "temperature"),
        get_eid(hass, "binary_sensor", "fan"),
        get_eid(hass, "binary_sensor", "thermal_shutdown"),
    ]
    with patch(f"{P}.health", AsyncMock(side_effect=PrimeConnectionError("down"))):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    for eid in ids:
        assert hass.states.get(eid).state == STATE_UNAVAILABLE
