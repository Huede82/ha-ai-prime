"""Setup / unload tests (client methods patched, no sockets)."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ai_prime.api import Health, PrimeAuthError, PrimeConnectionError
from custom_components.ai_prime.const import DOMAIN

SERIAL = "D89760046B9C"
COLORS = {"deep_red": 1, "cool_white": 2, "green": 3, "blue": 4, "warm_white": 5}
HEALTH = Health(45.6, True, False)
P = "custom_components.ai_prime.coordinator.PrimeClient"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, unique_id=SERIAL, data={CONF_HOST: "192.168.2.50"}
    )


def _patches(**overrides):
    values = {
        "identity": {"serial_number": SERIAL, "product": "Prime 16HD"},
        "colors": COLORS,
        "schedule_enabled": True,
        "health": HEALTH,
        "power": None,
    }
    values.update(overrides)
    return [
        patch(
            f"{P}.{k}",
            **({"side_effect": v} if isinstance(v, Exception) else {"return_value": v}),
        )
        for k, v in values.items()
    ]


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, **overrides) -> None:
    entry.add_to_hass(hass)
    patches = _patches(**overrides)
    for p in patches:
        p.start()
    try:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        for p in patches:
            p.stop()


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    entry = _entry()
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    assert coordinator.serial == SERIAL
    assert coordinator.data.colors == COLORS
    assert coordinator.data.schedule_enabled is True
    assert coordinator.data.health == HEALTH
    assert coordinator.power_model is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retry_when_offline(hass: HomeAssistant) -> None:
    entry = _entry()
    await _setup(hass, entry, colors=PrimeConnectionError("down"))
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retry_when_identity_fails(hass: HomeAssistant) -> None:
    entry = _entry()
    await _setup(hass, entry, identity=PrimeConnectionError("down"))
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_failure_starts_reauth(hass: HomeAssistant) -> None:
    entry = _entry()
    await _setup(hass, entry, colors=PrimeAuthError("nope"))
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(f["context"]["source"] == "reauth" for f in flows)
