"""Master light: brightness scales the whole channel mix."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CHANNELS, CONF_PRESET, MAX_VALUE
from .studio import effective
from .coordinator import PrimeConfigEntry, PrimeCoordinator
from .entity import PrimeEntity, apply_colors, clamp
from .preset import Preset

PARALLEL_UPDATES = 1

# A remembered mix dimmer than 10 % is treated as a schedule fade-out.
MIN_RESTORE_MAX = 100


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrimeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PrimeLight(entry.runtime_data, entry)])


class PrimeLight(PrimeEntity, LightEntity):
    """Brightness-only light over all five channels."""

    _attr_name = None
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: PrimeCoordinator, entry: PrimeConfigEntry) -> None:
        super().__init__(coordinator, "light")
        self._entry = entry
        # Only mixes written through HA are remembered, never plain polls:
        # the lamp's own schedule fade-out would otherwise become the "on" value.
        self._last_mix: dict[str, int] | None = None

    def _remember(self, colors: Mapping[str, int]) -> None:
        if max(colors.values(), default=0) > 0:
            self._last_mix = dict(colors)

    @property
    def _max(self) -> int:
        return max(self.coordinator.data.colors.values(), default=0)

    @property
    def is_on(self) -> bool:
        return self._max > 0

    @property
    def brightness(self) -> int:
        if self._max == 0:
            return 0
        return max(1, round(self._max / MAX_VALUE * 255))

    def _fallback_mix(self) -> dict[str, int]:
        if self._last_mix and max(self._last_mix.values()) >= MIN_RESTORE_MAX:
            return dict(self._last_mix)
        raw = effective(self.hass, self._entry).get(CONF_PRESET)
        if raw:
            try:
                peak = Preset.from_dict(raw).peak_mix()
            except (KeyError, TypeError, ValueError):
                peak = {}
            if max(peak.values(), default=0) > 0:
                return peak
        return {c: MAX_VALUE for c in CHANNELS}

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on and ATTR_BRIGHTNESS not in kwargs:
            return

        def build(current: dict[str, int]) -> dict[str, int]:
            on = max(current.values(), default=0) > 0
            base = current if on else self._fallback_mix()
            if ATTR_BRIGHTNESS not in kwargs:
                return base
            target = round(kwargs[ATTR_BRIGHTNESS] / 255 * MAX_VALUE)
            top = max(base.values())
            return {c: clamp(v * target / top) for c, v in base.items()}

        self._remember(await apply_colors(self.coordinator, build))

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._remember(self.coordinator.data.colors)
        await apply_colors(self.coordinator, {c: 0 for c in CHANNELS})
