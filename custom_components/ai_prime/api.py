"""Async client for the AquaIllumination Prime local HTTP API (no HA imports)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import CHANNELS, MAX_VALUE


AUTH_COOKIE = "prime_auth"


class PrimeError(Exception):
    """Base error."""


class PrimeConnectionError(PrimeError):
    """Lamp unreachable, timed out or answered with something that is not API JSON."""


class PrimeAuthError(PrimeError):
    """Lamp rejected the credentials (HTTP 401)."""


class PrimeApiError(PrimeError):
    """Lamp answered with a non-zero response_code."""

    def __init__(self, code: int) -> None:
        super().__init__(f"Lamp returned response_code {code}")
        self.code = code


@dataclass(frozen=True)
class Health:
    """Lamp health readings."""

    celsius: float
    fan_on: bool
    thermal_shutdown: bool


class PrimeClient:
    """Client for one lamp."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        password: str | None = None,
        timeout: float = 10,
    ) -> None:
        self._base = f"http://{host}"
        self._session = session
        self._password = password
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        # Session cookie from the lamp's form login. Sent as an explicit header
        # so it never lands in the shared HA session's cookie jar.
        self._cookie: str | None = None
        self._login_lock = asyncio.Lock()

    async def _login(self) -> None:
        """Form login (POST /login, field pwd); success sets cookie prime_auth.

        A wrong password redirects back to login.html without a cookie.
        """
        stale = self._cookie
        async with self._login_lock:
            if self._cookie is not None and self._cookie != stale:
                return  # another request logged in meanwhile
            try:
                async with self._session.post(
                    f"{self._base}/login",
                    data={"pwd": self._password or ""},
                    timeout=self._timeout,
                    allow_redirects=False,
                ) as resp:
                    morsel = resp.cookies.get(AUTH_COOKIE)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                raise PrimeConnectionError(str(err) or type(err).__name__) from err
            if morsel is None or not morsel.value:
                self._cookie = None
                raise PrimeAuthError("Lamp rejected the password")
            self._cookie = morsel.value

    def _headers(self) -> dict[str, str]:
        return {"Cookie": f"{AUTH_COOKIE}={self._cookie}"} if self._cookie else {}

    async def _request(
        self, method: str, path: str, json: Any | None = None
    ) -> dict[str, Any]:
        if self._password and self._cookie is None and path != "/api/identity":
            await self._login()
        try:
            return await self._request_once(method, path, json)
        except PrimeAuthError:
            if not self._password:
                raise
        # Session expired (lamp reboot or timeout): log in again, retry once.
        self._cookie = None
        await self._login()
        return await self._request_once(method, path, json)

    async def _request_once(
        self, method: str, path: str, json: Any | None
    ) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                f"{self._base}{path}",
                json=json,
                headers=self._headers(),
                timeout=self._timeout,
                allow_redirects=False,
            ) as resp:
                if resp.status == 401:
                    raise PrimeAuthError("Lamp requires valid credentials")
                if resp.status != 200:
                    raise PrimeConnectionError(f"HTTP {resp.status} for {path}")
                try:
                    data = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as err:
                    raise PrimeConnectionError(f"Non-JSON answer for {path}") from err
        except PrimeError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise PrimeConnectionError(str(err) or type(err).__name__) from err
        if not isinstance(data, dict):
            raise PrimeConnectionError(f"Unexpected payload for {path}")
        code = data.get("response_code")
        if code is None:
            raise PrimeConnectionError(f"Missing response_code for {path}")
        if code != 0:
            raise PrimeApiError(code)
        return data

    @staticmethod
    def _strip(data: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in data.items() if k != "response_code"}

    async def identity(self) -> dict[str, Any]:
        return self._strip(await self._request("GET", "/api/identity"))

    async def colors(self) -> dict[str, int]:
        data = await self._request("GET", "/api/colors")
        try:
            return {c: int(data[c]) for c in CHANNELS}
        except (KeyError, TypeError, ValueError) as err:
            raise PrimeConnectionError("Incomplete colors payload") from err

    async def set_colors(self, values: Mapping[str, int]) -> None:
        missing = [c for c in CHANNELS if c not in values]
        if missing:
            raise ValueError(f"Missing channels: {', '.join(missing)}")
        body = {c: max(0, min(MAX_VALUE, int(values[c]))) for c in CHANNELS}
        await self._request("POST", "/api/colors", json=body)

    async def power(self) -> dict[str, Any] | None:
        """Power table of the first device, or None if the lamp does not report it."""
        try:
            data = await self._request("GET", "/api/power")
        except (PrimeConnectionError, PrimeApiError):
            return None
        try:
            device = data["devices"][0]
            result = {
                "max_power": int(device["max_power"]),
                "normal": {c: int(device["normal"][c]) for c in CHANNELS},
            }
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        if result["max_power"] <= 0 or not any(result["normal"].values()):
            return None
        return result

    async def schedule_enabled(self) -> bool:
        data = await self._request("GET", "/api/schedule/enable")
        enable = data.get("enable")
        if not isinstance(enable, bool):
            raise PrimeConnectionError("Incomplete schedule payload")
        return enable

    async def set_schedule(self, enabled: bool) -> None:
        await self._request("PUT", "/api/schedule/enable", json={"enable": enabled})

    async def schedule(self) -> dict[str, Any]:
        return self._strip(await self._request("GET", "/api/schedule"))

    async def set_schedule_points(self, payload: dict[str, Any]) -> None:
        """Replace the lamp's own schedule (``POST /api/schedule``)."""
        await self._request("POST", "/api/schedule", json=payload)

    async def health(self) -> Health:
        data = await self._request("GET", "/api/temperature")
        try:
            celsius = float(data["celsius"])
            fan = data["fan_status"]
            shutdown = data["thermal_shutdown_status"]
        except (KeyError, TypeError, ValueError) as err:
            raise PrimeConnectionError("Incomplete temperature payload") from err
        if not isinstance(fan, bool) or not isinstance(shutdown, bool):
            raise PrimeConnectionError("Incomplete temperature payload")
        return Health(celsius, fan, shutdown)
