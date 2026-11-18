"""Lamp temperature sensor."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfTemperature
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PrimeConfigEntry, PrimeCoordinator
from .entity import PrimeEntity
from .nature.moon import illumination

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrimeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PrimeTemperatureSensor(coordinator),
            PrimePowerSensor(coordinator),
            PrimeCloudCoverSensor(coordinator),
            PrimeNatureTargetSensor(coordinator),
            PrimeMoonPhaseSensor(coordinator),
        ]
    )


class PrimeTemperatureSensor(PrimeEntity, SensorEntity):
    """Internal lamp temperature."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: PrimeCoordinator) -> None:
        super().__init__(coordinator, "temperature")

    @property
    def native_value(self) -> float:
        return self.coordinator.data.health.celsius


class PrimePowerSensor(PrimeEntity, SensorEntity):
    """Power draw computed from the current channel mix."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: PrimeCoordinator) -> None:
        super().__init__(coordinator, "power")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.power_model is not None

    @property
    def native_value(self) -> float | None:
        model = self.coordinator.power_model
        return None if model is None else model.watts(self.coordinator.data.colors)


class _NatureSensor(PrimeEntity, SensorEntity):
    """Sensor refreshed by runner ticks."""

    _attr_native_unit_of_measurement = PERCENTAGE

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.nature.add_listener(self.async_write_ha_state))


class PrimeCloudCoverSensor(_NatureSensor):
    """Cloud cover at the weather location."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PrimeCoordinator) -> None:
        super().__init__(coordinator, "cloud_cover")

    @property
    def available(self) -> bool:
        nature = self.coordinator.nature
        return super().available and nature.active and nature.weather_available

    @property
    def native_value(self) -> float | None:
        weather = self.coordinator.nature.weather
        return None if weather is None else round(weather.cloud_cover * 100)


class PrimeNatureTargetSensor(_NatureSensor):
    """Current engine target as percent of the brightest channel."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: PrimeCoordinator) -> None:
        super().__init__(coordinator, "nature_target")

    @property
    def available(self) -> bool:
        nature = self.coordinator.nature
        return super().available and nature.active and nature.last_target is not None

    @property
    def native_value(self) -> float | None:
        target = self.coordinator.nature.last_target
        return None if target is None else max(target.channels.values()) / 10

    @property
    def extra_state_attributes(self) -> dict | None:
        target = self.coordinator.nature.last_target
        return None if target is None else {**target.channels, **target.factors}


class PrimeMoonPhaseSensor(_NatureSensor):
    """Moon illumination; always available."""

    def __init__(self, coordinator: PrimeCoordinator) -> None:
        super().__init__(coordinator, "moon_phase")

    @property
    def native_value(self) -> float:
        return round(illumination(dt_util.utcnow()) * 100)
