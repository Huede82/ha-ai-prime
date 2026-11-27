"""AquaIllumination Prime integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from . import websocket_api
from .api import PrimeClient, PrimeError
from .const import CONF_FOLLOW, CONF_LAMP_WRITE_PENDING, DOMAIN
from .demo import Demo
from .coordinator import PrimeConfigEntry, PrimeCoordinator
from .frontend import async_register_frontend
from .library import migrate_v1
from .nature.runner import NatureRunner
from .studio import Studio, followers

_LOGGER = logging.getLogger(__name__)

_WS_FLAG = f"{DOMAIN}_ws_registered"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the studio websocket commands and the card once."""
    if not hass.data.get(_WS_FLAG):
        hass.data[_WS_FLAG] = True
        websocket_api.async_register(hass)
    await async_register_frontend(hass)
    return True


async def _options_updated(hass: HomeAssistant, entry: PrimeConfigEntry) -> None:
    """Apply option changes live; never reload the entry."""
    runner = entry.runtime_data.nature
    if runner is not None:
        await runner.apply_options()


async def async_migrate_entry(hass: HomeAssistant, entry: PrimeConfigEntry) -> bool:
    """v1 (single preset) -> v2 (preset library)."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry, options=migrate_v1(entry.options), version=2
        )
    return True


def _refresh_followers_when_loaded(hass: HomeAssistant, entry: PrimeConfigEntry) -> None:
    """A follower set up before its leader caches its own preset: refresh it later.

    A lamp write the follower deferred while its leader was missing runs now.
    """

    def _on_state() -> None:
        if entry.state is not ConfigEntryState.LOADED:
            return

        async def _refresh() -> None:
            for other in followers(hass, entry):
                await other.runtime_data.studio.refresh(
                    lamp_write=bool(other.options.get(CONF_LAMP_WRITE_PENDING))
                )

        entry.async_create_task(hass, _refresh(), "ai_prime refresh followers")

    entry.async_on_unload(entry.async_on_state_change(_on_state))


async def async_setup_entry(hass: HomeAssistant, entry: PrimeConfigEntry) -> bool:
    coordinator = PrimeCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    coordinator.studio = Studio(hass, entry)
    coordinator.nature = NatureRunner(hass, coordinator, entry)
    coordinator.demo = Demo(hass, coordinator, entry)
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    _refresh_followers_when_loaded(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PrimeConfigEntry) -> bool:
    # Platforms first: entities are removed while still "on", so RestoreEntity
    # keeps the nature switch state across a reload.
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok and entry.runtime_data.demo is not None:
        await entry.runtime_data.demo.stop()
    if ok and entry.runtime_data.nature is not None:
        await entry.runtime_data.nature.stop()
    if ok:
        # Followers fall back to their own options while the leader is gone.
        for other in followers(hass, entry):
            await other.runtime_data.studio.refresh(lamp_write=False)
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: PrimeConfigEntry) -> None:
    """Hand the lamp back to its internal schedule when the integration is removed."""
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.options.get(CONF_FOLLOW) == entry.entry_id:
            hass.config_entries.async_update_entry(
                other, options={**other.options, CONF_FOLLOW: None}
            )
            # The follower goes back to its own preset: write it to its lamp.
            if other.state is ConfigEntryState.LOADED:
                await other.runtime_data.studio.refresh(lamp_write=True)
    client = PrimeClient(
        entry.data[CONF_HOST],
        async_get_clientsession(hass),
        entry.data.get(CONF_PASSWORD) or None,
    )
    try:
        await client.set_schedule(True)
    except PrimeError as err:
        _LOGGER.warning("Could not re-enable lamp schedule on removal: %s", err)
