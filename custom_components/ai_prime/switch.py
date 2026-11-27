"""Internal schedule on/off switch."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import PrimeError
from .coordinator import PrimeConfigEntry, PrimeCoordinator
from .entity import PrimeEntity, apply_schedule, stop_demo

PARALLEL_UPDATES = 1
RESTORE_RETRY_S = 60
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrimeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PrimeScheduleSwitch(coordinator),
            PrimeNatureSwitch(coordinator),
            PrimeLightningSwitch(coordinator),
        ]
    )


class PrimeScheduleSwitch(PrimeEntity, SwitchEntity):
    """Whether the lamp runs its own schedule."""

    def __init__(self, coordinator: PrimeCoordinator) -> None:
        super().__init__(coordinator, "internal_schedule")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.schedule_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await apply_schedule(self.coordinator, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await apply_schedule(self.coordinator, False)


class _RunnerSwitch(PrimeEntity, SwitchEntity, RestoreEntity):
    """Base for switches backed by the nature runner."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.nature.add_listener(self.async_write_ha_state))
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_ON:
            await self._restore()

    async def _restore(self) -> None:
        raise NotImplementedError


class PrimeNatureSwitch(_RunnerSwitch):
    """Nature mode on/off; restored after a restart."""

    def __init__(self, coordinator: PrimeCoordinator) -> None:
        super().__init__(coordinator, "nature_mode")

    @property
    def is_on(self) -> bool:
        return self.coordinator.nature.active

    _cancel_retry: CALLBACK_TYPE | None = None
    _restore_abandoned = False

    def _clear_retry(self) -> None:
        # Turn on/off and removal end the restore: an attempt still in flight
        # must not schedule another one.
        self._restore_abandoned = True
        if self._cancel_retry is not None:
            self._cancel_retry()
            self._cancel_retry = None

    async def _restore(self) -> None:
        async def attempt(_arg: Any) -> None:
            self._cancel_retry = None
            if self._restore_abandoned:
                return
            try:
                await self.coordinator.nature.start()
            except (HomeAssistantError, PrimeError) as err:
                if self._restore_abandoned:
                    return
                _LOGGER.warning(
                    "Could not restore nature mode (retrying in %s s): %s",
                    int(RESTORE_RETRY_S),
                    err,
                )
                self._cancel_retry = async_call_later(self.hass, RESTORE_RETRY_S, attempt)

        self.async_on_remove(async_at_started(self.hass, attempt))
        self.async_on_remove(self._clear_retry)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._clear_retry()
        await stop_demo(self.coordinator)
        await self.coordinator.nature.start()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._clear_retry()
        await stop_demo(self.coordinator)
        await self.coordinator.nature.stop()


class PrimeLightningSwitch(_RunnerSwitch):
    """Allow lightning during thunderstorms; default off."""

    def __init__(self, coordinator: PrimeCoordinator) -> None:
        super().__init__(coordinator, "lightning")

    @property
    def is_on(self) -> bool:
        return self.coordinator.nature.lightning_enabled

    async def _restore(self) -> None:
        await self.coordinator.nature.set_lightning(True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.nature.set_lightning(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.nature.set_lightning(False)
