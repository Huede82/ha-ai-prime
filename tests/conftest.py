"""Shared test fixtures and helpers."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiohttp.test_utils import TestServer
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_component

from custom_components.ai_prime.api import Health
from custom_components.ai_prime.const import CHANNELS, DOMAIN
from custom_components.ai_prime.library import migrate_v1

from .fake_lamp import make_fake_lamp

SERIAL = "D89760046B9C"
P = "custom_components.ai_prime.coordinator.PrimeClient"
# Real lamp (firmware 2.5.1), mW; sum(normal) == max_power.
POWER = {
    "max_power": 48000,
    "normal": {"green": 3073, "blue": 3073, "deep_red": 2436, "cool_white": 23306, "warm_white": 16112},
}
IDENTITY = {"serial_number": SERIAL, "product": "Prime 16HD", "firmware": "1.2.3"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of custom integrations in all tests."""
    yield


def pytest_collection_modifyitems(items):
    # HA core's http setup (a manifest dependency) trips this aiohttp warning.
    # A marker, unlike the ini option, still wins over `-W error`.
    mark = pytest.mark.filterwarnings("ignore::aiohttp.web_exceptions.NotAppKeyWarning")
    for item in items:
        item.add_marker(mark)


@pytest.fixture(autouse=True)
def frontend_stub(request):
    """The manifest depends on ``frontend``; hass_frontend is not installed in the test venv."""
    if "hass" in request.fixturenames:
        hass = request.getfixturevalue("hass")
        mock_component(hass, "frontend")
        # add_extra_js_url stores URLs here; the real frontend component creates it.
        hass.data.setdefault("frontend_extra_module_url", set())


@pytest.fixture
async def lamp(socket_enabled):
    """A fake lamp over real HTTP; the coordinator's session is patched to reach it."""
    app, state = make_fake_lamp()
    server = TestServer(app)
    await server.start_server()
    async with aiohttp.ClientSession() as session:
        with patch(
            "custom_components.ai_prime.coordinator.async_get_clientsession",
            return_value=session,
        ):
            yield server, state
    await server.close()


@pytest.fixture
def stop_patches():
    with ExitStack() as stack:
        yield stack


def get_eid(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{SERIAL}_{key}")
    assert entity_id is not None
    return entity_id


async def setup_entry(
    hass: HomeAssistant,
    stack: ExitStack,
    colors: dict[str, int] | None = None,
    schedule: bool = False,
    options: dict | None = None,
    health: Health | None = None,
    power: dict | None = None,
):
    """Set up an entry with patched client (power=None: lamp without /api/power); return (entry, calls manager, state)."""
    manager = MagicMock()
    state = {"colors": dict(colors or {c: 0 for c in CHANNELS}), "schedule": schedule}

    async def set_colors(values):
        state["colors"] = dict(values)
        manager.set_colors(dict(values))

    async def set_schedule(enabled):
        state["schedule"] = enabled
        manager.set_schedule(enabled)

    patches = [
        patch(f"{P}.identity", AsyncMock(return_value=dict(IDENTITY))),
        patch(f"{P}.power", AsyncMock(return_value=power)),
        patch(f"{P}.colors", AsyncMock(side_effect=lambda: dict(state["colors"]))),
        patch(f"{P}.schedule_enabled", AsyncMock(side_effect=lambda: state["schedule"])),
        patch(f"{P}.health", AsyncMock(return_value=health or Health(45.6, True, False))),
        patch(f"{P}.set_colors", side_effect=set_colors),
        patch(f"{P}.set_schedule", side_effect=set_schedule),
        patch(f"{P}.schedule", AsyncMock(return_value={"name": "default", "ramps": []})),
        patch(f"{P}.set_schedule_points", AsyncMock()),
    ]
    for p in patches:
        stack.enter_context(p)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        data={CONF_HOST: "192.168.2.50"},
        version=2,
        options=migrate_v1(options) if options and "preset" in options else (options or {}),
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, manager, state
