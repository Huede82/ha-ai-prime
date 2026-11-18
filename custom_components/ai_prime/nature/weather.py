"""Open-Meteo weather source (cloud cover and thunderstorm flag)."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiohttp

from . import Location

_LOGGER = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
THUNDERSTORM_CODES = frozenset({95, 96, 99})
REQUEST_TIMEOUT_S = 10
RETRY_AFTER = timedelta(minutes=5)


@dataclass(frozen=True)
class Weather:
    """Current weather; cloud_cover is 0..1."""

    cloud_cover: float
    thunderstorm: bool
    fetched_at: datetime


class _BadPayload(Exception):
    """The response was not a usable Open-Meteo payload."""


def _parse(data: object) -> tuple[float, bool]:
    if not isinstance(data, dict) or not isinstance(data.get("current"), dict):
        raise _BadPayload("missing current")
    current = data["current"]
    cloud = current.get("cloud_cover")
    code = current.get("weather_code")
    for value in (cloud, code):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _BadPayload("bad values")
    if not math.isfinite(cloud) or not math.isfinite(code) or code != int(code):
        raise _BadPayload("bad values")
    cloud = float(cloud)
    code = int(code)
    return min(1.0, max(0.0, cloud / 100)), code in THUNDERSTORM_CODES


class WeatherSource:
    """Fetches and caches the current weather for the home location."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        loc_provider: Callable[[], Location],
        refresh: timedelta = timedelta(minutes=15),
        stale_after: timedelta = timedelta(hours=1),
        url: str = OPEN_METEO_URL,
    ) -> None:
        self._session = session
        self._loc_provider = loc_provider
        self._refresh = refresh
        self._stale_after = stale_after
        self._url = url
        self._last: Weather | None = None
        self._last_coords: tuple[float, float] | None = None
        self._attempt_coords: tuple[float, float] | None = None
        self._next_attempt: datetime | None = None
        self._outage = False
        self._fresh = False
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        """True when the last get() had a fetched value younger than stale_after."""
        return self._fresh

    async def get(self, now: datetime) -> Weather:
        """Return the current weather, fetching when due. Never raises."""
        async with self._lock:
            try:
                await self._refresh_if_due(now)
            except Exception:  # noqa: BLE001 - last safety net, get() never raises
                if not self._outage:
                    _LOGGER.exception("Unexpected error fetching weather")
                    self._outage = True
                self._next_attempt = now + RETRY_AFTER
            self._fresh = (
                self._last is not None
                and now - self._last.fetched_at < self._stale_after
            )
            if self._fresh:
                return self._last
            return Weather(0.0, False, now)

    def _failed(self, err: object) -> None:
        if not self._outage:
            _LOGGER.warning("Open-Meteo weather unavailable: %s", err)
            self._outage = True

    async def _refresh_if_due(self, now: datetime) -> None:
        try:
            loc = self._loc_provider()
            lat, lon = float(loc.latitude), float(loc.longitude)
            if not (math.isfinite(lat) and math.isfinite(lon)):
                raise ValueError("non-finite coordinates")
            coords = (round(lat, 2), round(lon, 2))
        except Exception as err:  # noqa: BLE001 - provider must not break get()
            self._failed(f"location unavailable: {err}")
            return
        due = (
            coords != self._attempt_coords
            or self._next_attempt is None
            or now >= self._next_attempt
        )
        if not due:
            return
        self._attempt_coords = coords
        try:
            cloud, storm = await self._fetch(coords)
        except (aiohttp.ClientError, TimeoutError, _BadPayload, ValueError) as err:
            self._failed(err)
            self._next_attempt = now + RETRY_AFTER
            if coords != self._last_coords:
                self._last = None
            return
        if self._outage:
            _LOGGER.info("Open-Meteo weather available again")
            self._outage = False
        self._last = Weather(cloud, storm, now)
        self._last_coords = coords
        self._next_attempt = now + self._refresh

    async def _fetch(self, coords: tuple[float, float]) -> tuple[float, bool]:
        params = {
            "latitude": str(coords[0]),
            "longitude": str(coords[1]),
            "current": "cloud_cover,weather_code",
        }
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S)
        async with self._session.get(self._url, params=params, timeout=timeout) as resp:
            if resp.status != 200:
                raise _BadPayload(f"HTTP {resp.status}")
            data = await resp.json(content_type=None)
        return _parse(data)
