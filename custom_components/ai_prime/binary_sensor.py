"""Fan and thermal shutdown binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Health
from .coordinator import PrimeConfigEntry, PrimeCoordinator
from .entity import PrimeEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class PrimeBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Health], bool]


DESCRIPTIONS = (
    PrimeBinaryDescription(
        key="fan",
        translation_key="fan",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda h: h.fan_on,
    ),
    PrimeBinaryDescription(
        key="thermal_shutdown",
        translation_key="thermal_shutdown",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda h: h.thermal_shutdown,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrimeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        PrimeHealthBinarySensor(entry.runtime_data, d) for d in DESCRIPTIONS
    )


class PrimeHealthBinarySensor(PrimeEntity, BinarySensorEntity):
    """A boolean health reading."""

    entity_description: PrimeBinaryDescription

    def __init__(
        self, coordinator: PrimeCoordinator, description: PrimeBinaryDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.coordinator.data.health)
