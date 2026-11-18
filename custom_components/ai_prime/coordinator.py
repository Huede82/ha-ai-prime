"""Data update coordinator for the AquaIllumination Prime."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    Health,
    PrimeApiError,
    PrimeAuthError,
    PrimeClient,
    PrimeConnectionError,
    PrimeError,
)
from .const import (
    CONF_FOLLOW,
    CONF_LAMP_WRITE_PENDING,
    CONF_ORIGINAL_CAPTURED,
    CONF_PRESET,
    CONF_PRESETS,
    DOMAIN,
    SCAN_INTERVAL,
)
from .library import ORIGINAL_ID, to_lamp_schedule
from .preset import Preset
from .power import PowerModel
from .studio import effective, leader_entry

if TYPE_CHECKING:
    from .nature.runner import NatureRunner
    from .studio import Studio

_LOGGER = logging.getLogger(__name__)

type PrimeConfigEntry = ConfigEntry[PrimeCoordinator]

# Throttle for the pending-write retry after connection failures (seconds).
RETRY_MIN = 300.0
RETRY_MAX = 1800.0


class LampWrite(StrEnum):
    """Outcome of ``write_active_schedule``."""

    OK = "ok"
    PENDING = "pending"  # unreachable or leader not loaded yet: retried later
    REJECTED = "rejected"  # lamp refused the schedule: not retried


@dataclass
class PrimeData:
    """One polling snapshot of the lamp."""

    colors: dict[str, int]
    schedule_enabled: bool
    health: Health


class PrimeCoordinator(DataUpdateCoordinator[PrimeData]):
    """Polls the lamp every SCAN_INTERVAL."""

    config_entry: PrimeConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PrimeConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=SCAN_INTERVAL,
        )
        self.client = PrimeClient(
            entry.data[CONF_HOST],
            async_get_clientsession(hass),
            entry.data.get(CONF_PASSWORD) or None,
        )
        self.serial: str = entry.unique_id or ""
        self.identity: dict[str, Any] = {}
        self.write_lock = asyncio.Lock()
        self.power_model: PowerModel | None = None
        self.nature: NatureRunner | None = None
        self.studio: Studio | None = None
        self._retry_running = False
        self._retry_delay = 0.0
        self._retry_at = 0.0  # monotonic time before which no retry is started

    def _set_options(self, **changes: Any) -> None:
        """Update entry options; the update listener only re-applies runner options."""
        options = self.config_entry.options
        if all(options.get(k) == v for k, v in changes.items()):
            return
        self.hass.config_entries.async_update_entry(
            self.config_entry, options={**options, **changes}
        )

    async def write_active_schedule(self) -> LampWrite:
        """Back up the lamp's original schedule once, then write the active preset.

        PENDING (write marked pending) when the lamp could not be reached or the
        followed leader is not loaded yet; REJECTED (not pending, not retried)
        when the lamp refused the schedule or sent a malformed one.
        """
        changes: dict[str, Any] = {}
        original_entry: dict[str, Any] | None = None
        result = LampWrite.OK
        if self.config_entry.options.get(CONF_FOLLOW) and leader_entry(
            self.hass, self.config_entry
        ) is None:
            # Never write the follower's own schedule in place of the leader's;
            # the leader's setup refreshes its followers with the pending write.
            self._set_options(**{CONF_LAMP_WRITE_PENDING: True})
            return LampWrite.PENDING
        async with self.write_lock:
            options = self.config_entry.options
            if not options.get(CONF_ORIGINAL_CAPTURED):
                try:
                    original = await self.client.schedule()
                    original_entry = {
                        "name": "Original (Lampe)",
                        "ramps": {
                            r["color"]: [[p["time"], p["intensity"]] for p in r["points"]]
                            for r in original.get("ramps", [])
                        },
                        "readonly": True,
                    }
                except PrimeApiError as err:
                    _LOGGER.warning("Lamp refused to send its original schedule: %s", err)
                    result = LampWrite.REJECTED
                except (KeyError, TypeError, AttributeError) as err:
                    _LOGGER.warning("Lamp sent a malformed original schedule: %r", err)
                    result = LampWrite.REJECTED
                except PrimeError as err:
                    _LOGGER.warning("Could not read the lamp's original schedule: %s", err)
                    result = LampWrite.PENDING
                else:
                    changes[CONF_ORIGINAL_CAPTURED] = True
            if result is LampWrite.OK:
                stored = effective(self.hass, self.config_entry).get(CONF_PRESET)
                if stored is not None:
                    try:
                        await self.client.set_schedule_points(
                            to_lamp_schedule(Preset.from_dict(stored))
                        )
                    except PrimeApiError as err:
                        _LOGGER.warning("Lamp rejected the schedule: %s", err)
                        result = LampWrite.REJECTED
                    except PrimeError as err:
                        _LOGGER.warning("Could not write the preset to the lamp: %s", err)
                        result = LampWrite.PENDING
            changes[CONF_LAMP_WRITE_PENDING] = result is LampWrite.PENDING
            self._note_retry(result)
        # Outside the lock: the options listener may tick the runner (takes the lock).
        # Merge into the CURRENT presets: studio edits may have landed during the I/O.
        if original_entry is not None:
            current = dict(self.config_entry.options.get(CONF_PRESETS) or {})
            if ORIGINAL_ID not in current:
                current[ORIGINAL_ID] = original_entry
                changes[CONF_PRESETS] = current
        self._set_options(**changes)
        return result

    def _note_retry(self, result: LampWrite) -> None:
        """Back off exponentially (RETRY_MIN..RETRY_MAX) after connection failures."""
        if result is LampWrite.PENDING:
            self._retry_delay = min(max(self._retry_delay * 2, RETRY_MIN), RETRY_MAX)
            self._retry_at = monotonic() + self._retry_delay
        else:
            self._retry_delay = 0.0
            self._retry_at = 0.0

    def _schedule_pending_retry(self) -> None:
        if (
            self._retry_running
            or not self.config_entry.options.get(CONF_LAMP_WRITE_PENDING)
            or monotonic() < self._retry_at
        ):
            return
        self._retry_running = True

        async def _retry() -> None:
            try:
                await self.write_active_schedule()
            finally:
                self._retry_running = False

        self.config_entry.async_create_background_task(
            self.hass, _retry(), "ai_prime pending lamp write"
        )

    async def _async_setup(self) -> None:
        """Fetch the static identity once."""
        try:
            self.identity = await self.client.identity()
            power = await self.client.power()
        except PrimeAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (PrimeConnectionError, PrimeApiError) as err:
            raise UpdateFailed(str(err)) from err
        self.serial = self.identity.get("serial_number", self.serial)
        # Older firmware has no /api/power: the power sensor stays unavailable.
        self.power_model = PowerModel.from_power(power) if power else None

    async def _async_update_data(self) -> PrimeData:
        try:
            data = PrimeData(
                colors=await self.client.colors(),
                schedule_enabled=await self.client.schedule_enabled(),
                health=await self.client.health(),
            )
        except PrimeAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (PrimeConnectionError, PrimeApiError) as err:
            raise UpdateFailed(str(err)) from err
        self._schedule_pending_retry()
        return data
