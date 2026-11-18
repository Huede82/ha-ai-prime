"""One percent slider per channel."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CHANNELS
from .coordinator import PrimeConfigEntry, PrimeCoordinator
from .entity import PrimeEntity, apply_colors, clamp

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrimeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(PrimeChannelNumber(coordinator, c) for c in CHANNELS)


class PrimeChannelNumber(PrimeEntity, NumberEntity):
    """Intensity of one colour channel in percent."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 0.1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: PrimeCoordinator, channel: str) -> None:
        super().__init__(coordinator, channel)
        self._channel = channel

    @property
    def native_value(self) -> float:
        return self.coordinator.data.colors[self._channel] / 10

    async def async_set_native_value(self, value: float) -> None:
        channel, target = self._channel, clamp(value * 10)
        await apply_colors(self.coordinator, lambda cur: {**cur, channel: target})
