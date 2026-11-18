"""Nature mode runner: ticks the engine every minute and writes the lamp."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from ..api import PrimeError
from ..const import (
    CONF_ACTIVE_PRESET,
    CONF_CLOUD_STRENGTH,
    CONF_LIGHTNING_MAX,
    CONF_LIGHTNING_MIN,
    CONF_MOON_CHANNEL,
    CONF_MOON_LOCATION,
    CONF_MOON_MAX,
    CONF_MOON_MODE,
    CONF_PRESET,
    CONF_PRESETS,
    CONF_SUN_LOCATION,
    CONF_SUN_MODE,
    CONF_WEATHER_LOCATION,
)
from ..preset import Preset
from ..studio import effective
from . import Location, TimeMode, zone_for_longitude
from .engine import NatureSettings, Target, compute
from .lightning import LightningScheduler
from .moon import illumination, moon_window
from .sun import sun_window
from .weather import Weather, WeatherSource

if TYPE_CHECKING:
    from ..coordinator import PrimeConfigEntry, PrimeCoordinator

_LOGGER = logging.getLogger(__name__)

TICK = timedelta(seconds=60)
IDLE_SLEEP_S = 30
MAX_ERRORS = 3
FLASH_CHANNELS = ("cool_white", "blue")
FLASH_VALUE = 1000
RESTORE_RETRIES = 2
RESTORE_RETRY_PAUSE_S = 1
_sleep = asyncio.sleep  # module alias so tests can patch timing


class NatureRunner:
    """Drives the lamp from the engine while nature mode is on."""

    def __init__(
        self, hass: HomeAssistant, coordinator: PrimeCoordinator, entry: PrimeConfigEntry
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.entry = entry
        self.active = False
        self.lightning_enabled = False
        self.last_target: Target | None = None
        self.weather: Weather | None = None
        self._dark = True
        self._errors = 0
        self._flash_on = False
        self._lifecycle = asyncio.Lock()
        self._last_written: dict[str, int] | None = None
        self._unsub_timer: Callable[[], None] | None = None
        self._lightning_task: asyncio.Task | None = None
        self._listeners: list[Callable[[], None]] = []
        self._cache: tuple[Any, tuple[float, float] | None, list] | None = None
        self._weather_source = WeatherSource(
            async_get_clientsession(hass), lambda: self._location(CONF_WEATHER_LOCATION)
        )
        self._rng = random.Random()
        self._preset: Preset | None = None
        self._settings = NatureSettings()
        self._scheduler = LightningScheduler(self._rng)
        self._load_options()

    @property
    def weather_available(self) -> bool:
        return self._weather_source.available

    # -- options -------------------------------------------------------
    def _home(self) -> Location:
        cfg = self.hass.config
        return Location(cfg.latitude, cfg.longitude, cfg.time_zone)

    def _location(self, key: str) -> Location:
        home = self._home()
        opt = effective(self.hass, self.entry).get(key)
        if not opt:
            return home
        lat, lon = float(opt["latitude"]), float(opt["longitude"])
        if (lat, lon) == (home.latitude, home.longitude):
            return home
        # No tz lookup: fixed offset from longitude; only matters for the
        # "local midnight" of the moon in anchored mode (accepted approximation).
        return Location(lat, lon, zone_for_longitude(lon))

    def _load_options(self) -> None:
        opts = effective(self.hass, self.entry)
        try:
            self._preset = Preset.from_dict(opts[CONF_PRESET])
        except (KeyError, TypeError, ValueError):
            self._preset = None
        self._settings = NatureSettings(
            cloud_strength=opts.get(CONF_CLOUD_STRENGTH, 50) / 100,
            moon_max=opts.get(CONF_MOON_MAX, 3) / 100,
            moon_channel=opts.get(CONF_MOON_CHANNEL, "blue"),
        )
        self._scheduler = LightningScheduler(
            self._rng,
            opts.get(CONF_LIGHTNING_MIN, 20),
            opts.get(CONF_LIGHTNING_MAX, 120),
        )
        self._cache = None

    async def apply_options(self) -> None:
        """Pick up changed options without restarting the runner."""
        self._load_options()
        if self.active:
            await self.tick(dt_util.now())

    # -- shared with the studio preview --------------------------------
    @property
    def preset(self) -> Preset | None:
        return self._preset

    @property
    def settings(self) -> NatureSettings:
        return self._settings

    def locations(self) -> tuple[Location, Location, TimeMode, Location, TimeMode]:
        """(home, sun_loc, sun_mode, moon_loc, moon_mode) as the engine sees them."""
        opts = effective(self.hass, self.entry)
        return (
            self._home(),
            self._location(CONF_SUN_LOCATION),
            opts.get(CONF_SUN_MODE, "anchored"),
            self._location(CONF_MOON_LOCATION),
            opts.get(CONF_MOON_MODE, "anchored"),
        )

    async def current_weather(self) -> Weather | None:
        """Current weather, or None when it could not be fetched (never raises)."""
        weather = await self._weather_source.get(dt_util.now())
        return weather if self._weather_source.available else None

    # -- listeners -----------------------------------------------------
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        def unsub() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return unsub

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # -- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        async with self._lifecycle:
            if self.active:
                return
            if self._preset is None:
                _LOGGER.warning("Nature mode needs a stored preset; not starting")
                return
            coord = self.coordinator
            async with coord.write_lock:
                try:
                    await coord.client.set_schedule(False)
                except PrimeError as err:
                    raise HomeAssistantError(
                        f"Could not disable lamp schedule: {err}"
                    ) from err
                coord.async_set_updated_data(replace(coord.data, schedule_enabled=False))
                self.active = True
            self._errors = 0
            self._last_written = None
            try:
                await self.tick(dt_util.now())
            except Exception as err:
                _LOGGER.exception("First nature mode tick failed; not starting")
                self.active = False
                await self._reenable_schedule()
                raise HomeAssistantError(f"Nature mode first tick failed: {err}") from err
            if not self.active:
                return
            self._unsub_timer = async_track_time_interval(self.hass, self._on_timer, TICK)
            self._lightning_task = self.entry.async_create_background_task(
                self.hass, self._lightning_loop(), "ai_prime nature lightning"
            )
        self._notify()

    async def _reenable_schedule(self) -> None:
        """Best-effort: hand the lamp back to its internal schedule."""
        coord = self.coordinator
        try:
            async with coord.write_lock:
                await coord.client.set_schedule(True)
                coord.async_set_updated_data(replace(coord.data, schedule_enabled=True))
        except PrimeError as err:
            _LOGGER.warning("Could not re-enable lamp schedule after failed start: %s", err)

    async def stop(self) -> None:
        async with self._lifecycle:
            if not self.active:
                return
            self.active = False
            if self._unsub_timer:
                self._unsub_timer()
                self._unsub_timer = None
            task, self._lightning_task = self._lightning_task, None
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            if self._flash_on and self.last_target is not None:
                await self._restore_after_flash()
            self._flash_on = False
            self.last_target = None
        self._notify()

    async def set_lightning(self, enabled: bool) -> None:
        self.lightning_enabled = enabled
        self._notify()

    async def reload_preset(self) -> None:
        """Re-read the lamp's schedule into the stored preset."""
        try:
            data = await self.coordinator.client.schedule()
            preset = Preset.from_api(data)
        except PrimeError as err:
            raise HomeAssistantError(f"Could not read lamp schedule: {err}") from err
        # The lamp is the source: store into the library without writing it back.
        options = self.entry.options
        active = options.get(CONF_ACTIVE_PRESET)
        stored = (options.get(CONF_PRESETS) or {}).get(active)
        writable = stored is not None and not stored.get("readonly")
        await self.coordinator.studio.save_preset(
            active if writable else None,
            stored["name"] if writable else (preset.name or "Lampe")[:60],
            preset.ramps,
            True,
            lamp_write=False,
        )

    # -- engine tick ---------------------------------------------------
    async def _on_timer(self, _now: datetime) -> None:
        await self.tick(dt_util.now())

    def _windows(self, day: date):
        sun_loc = self._location(CONF_SUN_LOCATION)
        moon_loc = self._location(CONF_MOON_LOCATION)
        opts = effective(self.hass, self.entry)
        sun_mode = opts.get(CONF_SUN_MODE, "anchored")
        moon_mode = opts.get(CONF_MOON_MODE, "anchored")
        key = (day, sun_loc, sun_mode, moon_loc, moon_mode)
        if self._cache is None or self._cache[0] != key:
            home = self._home()
            self._cache = (
                key,
                sun_window(sun_loc, day, sun_mode, home),
                moon_window(moon_loc, day, moon_mode, home),
            )
        return self._cache

    async def tick(self, now: datetime) -> None:
        """Compute the target for `now` and write it when it changed."""
        if self._preset is None:
            return
        local = dt_util.as_local(now)
        minute = local.hour * 60 + local.minute + local.second / 60
        _, sun, moon = await self.hass.async_add_executor_job(self._windows, local.date())
        moon_up = any(a <= minute <= b for a, b in moon)
        self.weather = await self._weather_source.get(now)
        target = compute(
            self._preset, minute, sun, moon_up, illumination(now), self.weather, self._settings
        )
        src = target.factors["source_minute"]
        base = self._preset.value_at(minute if src is None else src % 1440)
        self._dark = not moon_up and all(v == 0 for v in base.values())
        coord = self.coordinator
        async with coord.write_lock:
            if not self.active:
                return
            lamp_drifted = not self._flash_on and (
                coord.data.schedule_enabled or coord.data.colors != self._last_written
            )
            if target.channels != self._last_written or lamp_drifted:
                try:
                    if coord.data.schedule_enabled:
                        await coord.client.set_schedule(False)
                    await coord.client.set_colors(dict(target.channels))
                except PrimeError as err:
                    self._errors += 1
                    _LOGGER.warning("Nature mode write failed (%s): %s", self._errors, err)
                    if self._errors >= MAX_ERRORS:
                        self.last_target = None
                    self._notify()
                    return
                self._last_written = dict(target.channels)
                coord.async_set_updated_data(
                    replace(coord.data, colors=dict(target.channels), schedule_enabled=False)
                )
            self._errors = 0
            self.last_target = target
        self._notify()

    # -- lightning -----------------------------------------------------
    def _may_flash(self) -> bool:
        return bool(
            self.active
            and self.lightning_enabled
            and self.weather is not None
            and self.weather.thunderstorm
            and self.last_target is not None
            and not self._dark
        )

    async def _write_flash(self, flash: bool) -> bool:
        """POST engine value (plus flash channels); False if the POST failed."""
        async with self.coordinator.write_lock:
            if not self.active or self.last_target is None:
                return True
            return await self._send_target(flash)

    async def _send_target(self, flash: bool) -> bool:
        mix = dict(self.last_target.channels)
        if flash:
            mix.update({c: FLASH_VALUE for c in FLASH_CHANNELS})
        try:
            await self.coordinator.client.set_colors(mix)
        except PrimeError as err:
            _LOGGER.debug("Lightning write failed: %s", err)
            return False
        return True

    async def _restore_after_flash(self) -> None:
        """Write the engine value back; retry a failed POST a few times."""
        for attempt in range(RESTORE_RETRIES + 1):
            async with self.coordinator.write_lock:
                if self.last_target is None or await self._send_target(False):
                    return
            if attempt < RESTORE_RETRIES:
                await _sleep(RESTORE_RETRY_PAUSE_S)
        _LOGGER.warning("Could not restore lamp after lightning flash")
        self._last_written = None

    async def _lightning_step(self) -> None:
        if not self._may_flash():
            await _sleep(IDLE_SLEEP_S)
            return
        await _sleep(self._scheduler.next_pause())
        if not self._may_flash():
            return
        for on_s, off_s in self._scheduler.flash_pattern():
            self._flash_on = True
            try:
                await self._write_flash(True)
                await _sleep(on_s)
                await self._restore_after_flash()
            except asyncio.CancelledError:
                if self._flash_on:
                    await asyncio.shield(self._restore_after_flash())
                    self._flash_on = False
                raise
            self._flash_on = False
            await _sleep(off_s)

    async def _lightning_loop(self) -> None:
        while True:
            try:
                await self._lightning_step()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("Lightning loop iteration failed")
                self._flash_on = False
                self._last_written = None
                await _sleep(IDLE_SLEEP_S)
