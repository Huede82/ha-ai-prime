"""Shared entity base and write helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import PrimeError
from .const import CHANNELS, DOMAIN, MAX_VALUE
from .coordinator import PrimeCoordinator


def clamp(value: float) -> int:
    """Round and clamp to the lamp range."""
    return max(0, min(MAX_VALUE, round(value)))


async def _stop_nature(coordinator: PrimeCoordinator) -> None:
    """Stop nature mode before taking the write lock (the runner uses it too)."""
    if coordinator.nature is not None:
        await coordinator.nature.stop()


async def apply_colors(
    coordinator: PrimeCoordinator,
    values: Mapping[str, int] | Callable[[dict[str, int]], Mapping[str, int]],
) -> dict[str, int]:
    """Send a full channel mix; the internal schedule is disabled first.

    ``values`` may be a callable receiving the current colors; it runs under
    the write lock so parallel read-modify-write calls cannot lose a channel.
    Returns the mix that was sent.
    """
    await _stop_nature(coordinator)
    async with coordinator.write_lock:
        mix = values(dict(coordinator.data.colors)) if callable(values) else values
        safe = {c: clamp(mix.get(c, 0)) for c in CHANNELS}
        client = coordinator.client
        try:
            if coordinator.data.schedule_enabled:
                await client.set_schedule(False)
                coordinator.async_set_updated_data(
                    replace(coordinator.data, schedule_enabled=False)
                )
            await client.set_colors(safe)
        except PrimeError as err:
            raise HomeAssistantError(f"Could not set lamp colors: {err}") from err
        coordinator.async_set_updated_data(
            replace(coordinator.data, colors=safe, schedule_enabled=False)
        )
    await coordinator.async_request_refresh()
    return safe


async def apply_schedule(coordinator: PrimeCoordinator, enabled: bool) -> None:
    """Switch the internal schedule on or off."""
    if enabled:
        await _stop_nature(coordinator)
    async with coordinator.write_lock:
        try:
            await coordinator.client.set_schedule(enabled)
        except PrimeError as err:
            raise HomeAssistantError(f"Could not set lamp schedule: {err}") from err
        coordinator.async_set_updated_data(
            replace(coordinator.data, schedule_enabled=enabled)
        )
    await coordinator.async_request_refresh()


class PrimeEntity(CoordinatorEntity[PrimeCoordinator]):
    """Base class for all lamp entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PrimeCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.serial)},
            manufacturer="AquaIllumination",
            model=coordinator.identity.get("product"),
            name=coordinator.config_entry.title,
            sw_version=coordinator.identity.get("firmware"),
        )
