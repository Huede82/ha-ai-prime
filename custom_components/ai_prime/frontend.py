"""Serve the studio card and load it into every Lovelace frontend."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

URL_BASE = "/ai_prime_static"
CARD_FILE = "ai-prime-card.js"
_FLAG = f"{DOMAIN}_frontend_registered"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Register the static path and the card module once per HA run."""
    if hass.data.get(_FLAG):
        return
    hass.data[_FLAG] = True
    # The version is part of the path, so every sibling ES module (relative
    # import) gets a new URL after an upgrade; browsers and the companion app's
    # WebView otherwise keep serving stale modules from their heuristic cache.
    version = (await async_get_integration(hass, DOMAIN)).version
    folder = str(Path(__file__).parent / "frontend")
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(URL_BASE, folder, False),
            StaticPathConfig(f"{URL_BASE}/{version}", folder, False),
        ]
    )
    frontend.add_extra_js_url(hass, f"{URL_BASE}/{version}/{CARD_FILE}")
