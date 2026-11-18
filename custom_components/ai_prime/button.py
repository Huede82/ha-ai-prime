"""Button to re-read the preset from the lamp."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PrimeConfigEntry, PrimeCoordinator
from .entity import PrimeEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrimeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PrimeReloadPresetButton(entry.runtime_data)])


class PrimeReloadPresetButton(PrimeEntity, ButtonEntity):
    """Stores the lamp's current schedule as the nature-mode preset."""

    def __init__(self, coordinator: PrimeCoordinator) -> None:
        super().__init__(coordinator, "reload_preset")

    async def async_press(self) -> None:
        await self.coordinator.nature.reload_preset()
