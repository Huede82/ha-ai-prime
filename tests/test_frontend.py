"""Frontend delivery: static path and extra module URL."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.loader import async_get_integration
from homeassistant.setup import async_setup_component

from custom_components.ai_prime import frontend as frontend_mod
from custom_components.ai_prime.const import DOMAIN


async def test_frontend_registered(hass, hass_client) -> None:
    version = (await async_get_integration(hass, DOMAIN)).version
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    assert f"/ai_prime_static/ai-prime-card.js?v={version}" in hass.data["frontend_extra_module_url"]

    client = await hass_client()
    resp = await client.get("/ai_prime_static/ai-prime-card.js")
    assert resp.status == 200
    assert "ai-prime-card" in await resp.text()
    resp = await client.get("/ai_prime_static/model.js")
    assert resp.status == 200
    assert "max-age" not in resp.headers.get("Cache-Control", "")


async def test_frontend_registration_idempotent(hass) -> None:
    assert await async_setup_component(hass, "http", {})
    with patch("homeassistant.components.frontend.add_extra_js_url") as add:
        await frontend_mod.async_register_frontend(hass)
        await frontend_mod.async_register_frontend(hass)
    assert add.call_count == 1
