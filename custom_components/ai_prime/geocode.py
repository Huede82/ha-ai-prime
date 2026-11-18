"""Server-side place search via Open-Meteo geocoding."""

from __future__ import annotations

import aiohttp

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
REQUEST_TIMEOUT_S = 10


class GeocodeError(Exception):
    """The geocoding request failed or returned garbage."""


async def geocode(session: aiohttp.ClientSession, query: str, language: str) -> list[dict]:
    """Search places by name; returns up to 5 results with 4-decimal coordinates."""
    query = query.strip()
    if len(query) < 2:
        return []
    params = {
        "name": query,
        "count": 5,
        "language": "de" if language == "de" else "en",
        "format": "json",
    }
    try:
        async with session.get(
            GEOCODE_URL, params=params, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S)
        ) as resp:
            if resp.status != 200:
                raise GeocodeError(f"HTTP {resp.status}")
            data = await resp.json(content_type=None)
        results = data.get("results") or [] if isinstance(data, dict) else []
        return [
            {
                "name": str(r.get("name", "")),
                "country": str(r.get("country", "")),
                "admin1": str(r.get("admin1", "")),
                "latitude": round(float(r["latitude"]), 4),
                "longitude": round(float(r["longitude"]), 4),
            }
            for r in results
        ]
    except GeocodeError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError, AttributeError) as err:
        raise GeocodeError(str(err)) from err
