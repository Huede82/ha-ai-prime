"""Live demo: mirror the studio preview playback on the real lamp."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .api import PrimeError
from .const import CHANNELS, MAX_VALUE

if TYPE_CHECKING:
    from .coordinator import PrimeConfigEntry, PrimeCoordinator

_LOGGER = logging.getLogger(__name__)

WATCHDOG_S = 5


def _clamp(value: float) -> int:
    return max(0, min(MAX_VALUE, round(value)))


class Demo:
    """Takes the lamp over while the studio plays a preview, then hands it back."""

    def __init__(
        self, hass: HomeAssistant, coordinator: PrimeCoordinator, entry: PrimeConfigEntry
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.entry = entry
        self.active = False
        self.owner: Any = None  # the websocket connection that drives the demo
        self._lifecycle = asyncio.Lock()
        self._busy = False  # a frame (or the implicit start) is in flight
        self._stopping = False
        self._was_nature = False
        self._was_schedule = False
        self._colors: dict[str, int] = {}
        self._cancel_watchdog: CALLBACK_TYPE | None = None

    # -- watchdog ------------------------------------------------------
    def _arm_watchdog(self) -> None:
        if self._cancel_watchdog is not None:
            self._cancel_watchdog()
        self._cancel_watchdog = async_call_later(self.hass, WATCHDOG_S, self._on_watchdog)

    def _disarm_watchdog(self) -> None:
        if self._cancel_watchdog is not None:
            self._cancel_watchdog()
            self._cancel_watchdog = None

    @callback
    def _on_watchdog(self, _now: Any) -> None:
        self._cancel_watchdog = None
        _LOGGER.debug("Demo watchdog expired; restoring the lamp")
        self.entry.async_create_background_task(self.hass, self.stop(), "ai_prime demo watchdog")

    # -- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        """Remember the lamp state, pause nature mode and the internal schedule."""
        async with self._lifecycle:
            if self.active:
                return
            coord = self.coordinator
            runner = coord.nature
            self._was_nature = bool(runner is not None and runner.active)
            self._was_schedule = coord.data.schedule_enabled
            self._colors = dict(coord.data.colors)
            if self._was_nature:
                runner.pause()
            try:
                async with coord.write_lock:
                    if coord.data.schedule_enabled:
                        await coord.client.set_schedule(False)
                        coord.async_set_updated_data(
                            replace(coord.data, schedule_enabled=False)
                        )
            except PrimeError:
                if self._was_nature:
                    await runner.resume()
                raise
            self.active = True
            self._arm_watchdog()

    async def frame(self, values: Mapping[str, float], owner: Any = None) -> bool:
        """Show one frame (units 0-1000); False when dropped (previous one in flight)."""
        if self._busy or self._stopping:
            return False
        self._busy = True
        try:
            if not self.active:
                await self.start()
            if owner is not None:
                self.owner = owner
            self._arm_watchdog()
            safe = {c: _clamp(values.get(c, 0)) for c in CHANNELS}
            coord = self.coordinator
            async with coord.write_lock:
                if not self.active:
                    return False
                await coord.client.set_colors(safe)
            coord.async_set_updated_data(replace(coord.data, colors=safe))
            return True
        finally:
            self._busy = False

    async def stop(self) -> None:
        """Hand the lamp back: resume nature mode, re-enable the schedule or restore colors."""
        async with self._lifecycle:
            if not self.active:
                return
            self._stopping = True
            try:
                self._disarm_watchdog()
                self.owner = None
                coord = self.coordinator
                runner = coord.nature
                try:
                    if self._was_nature and runner is not None:
                        await runner.resume()
                    else:
                        async with coord.write_lock:
                            if self._was_schedule:
                                await coord.client.set_schedule(True)
                                coord.async_set_updated_data(
                                    replace(coord.data, schedule_enabled=True)
                                )
                            else:
                                await coord.client.set_colors(self._colors)
                                coord.async_set_updated_data(
                                    replace(coord.data, colors=dict(self._colors))
                                )
                except PrimeError as err:
                    _LOGGER.warning("Could not restore the lamp after the demo: %s", err)
                self.active = False
            finally:
                self._stopping = False
        await coord.async_request_refresh()

    def stop_for(self, owner: Any) -> Callable[[], None]:
        """Callback for a closing websocket connection: stop if it still owns the demo."""

        @callback
        def _closed() -> None:
            if self.active and self.owner is owner:
                self.entry.async_create_background_task(
                    self.hass, self.stop(), "ai_prime demo connection closed"
                )

        return _closed
