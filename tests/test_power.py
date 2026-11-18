"""Power model, power endpoint and power sensor tests."""

from __future__ import annotations

import aiohttp
import pytest
from aiohttp.test_utils import TestServer
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er

from custom_components.ai_prime.api import PrimeClient
from custom_components.ai_prime.const import CHANNELS
from custom_components.ai_prime.power import PowerModel

from .conftest import POWER, get_eid, setup_entry
from .fake_lamp import make_fake_lamp

MODEL = PowerModel.from_power(POWER)


@pytest.fixture
async def lamp(socket_enabled):
    app, state = make_fake_lamp()
    server = TestServer(app)
    await server.start_server()
    async with aiohttp.ClientSession() as session:
        yield PrimeClient(f"{server.host}:{server.port}", session), state
    await server.close()


async def test_power_parses_first_device(lamp) -> None:
    client, _ = lamp
    assert await client.power() == POWER


async def test_power_missing_endpoint_returns_none(lamp) -> None:
    client, state = lamp
    state["overrides"][("GET", "/api/power")] = (404, "Error 404: Not Found", {})
    assert await client.power() is None


@pytest.mark.parametrize(
    "payload",
    [
        '{"devices": [], "response_code": 0}',
        '{"devices": [{"max_power": 48000}], "response_code": 0}',
        '{"devices": [{"max_power": 0, "normal": {"green": 0, "blue": 0, "deep_red": 0,'
        ' "cool_white": 0, "warm_white": 0}}], "response_code": 0}',
        '{"response_code": 3}',
        "not json",
    ],
)
async def test_power_malformed_returns_none(lamp, payload) -> None:
    client, state = lamp
    state["overrides"][("GET", "/api/power")] = (200, payload, {})
    assert await client.power() is None


def test_power_model() -> None:
    assert MODEL.max_power == 48000
    assert sum(MODEL.normal.values()) == 48000
    assert MODEL.watts({c: 477 for c in CHANNELS}) == pytest.approx(22.9, abs=0.1)
    assert MODEL.watts({c: 1000 for c in CHANNELS}) == pytest.approx(48.0)
    assert MODEL.watts({c: 0 for c in CHANNELS}) == 0


async def test_coordinator_keeps_power_model(hass, stop_patches) -> None:
    entry, _, _ = await setup_entry(hass, stop_patches, power=POWER)
    assert entry.runtime_data.power_model == MODEL


async def test_power_sensor_value(hass, stop_patches) -> None:
    await setup_entry(hass, stop_patches, {c: 477 for c in CHANNELS}, power=POWER)
    st = hass.states.get(get_eid(hass, "sensor", "power"))
    assert float(st.state) == pytest.approx(22.9, abs=0.1)
    assert st.attributes["unit_of_measurement"] == "W"
    assert st.attributes["device_class"] == "power"


async def test_power_sensor_unavailable_without_power_table(hass, stop_patches) -> None:
    await setup_entry(hass, stop_patches)
    assert hass.states.get(get_eid(hass, "sensor", "power")).state == STATE_UNAVAILABLE


async def test_temperature_precision(hass, stop_patches) -> None:
    await setup_entry(hass, stop_patches)
    entry = er.async_get(hass).async_get(get_eid(hass, "sensor", "temperature"))
    assert entry.options["sensor"]["suggested_display_precision"] == 1
