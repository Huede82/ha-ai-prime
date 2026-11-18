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
    # No long-lived cache: only the entry URL carries ?v=, the sibling ES modules
    # (relative imports) must never be older than the entry after an upgrade.
    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(Path(__file__).parent / "frontend"), False)]
    )
    frontend.add_extra_js_url(hass, f"{URL_BASE}/{CARD_FILE}?v={version}")
