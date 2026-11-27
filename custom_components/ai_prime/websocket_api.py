"""Websocket API used by the AI Prime studio card."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import date
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from . import library
from .const import (
    CHANNELS,
    CONF_ACTIVE_PRESET,
    CONF_BRIGHTNESS,
    CONF_MOON_MAX,
    CONF_NIGHT_LIGHT,
    CONF_FOLLOW,
    CONF_LAMP_WRITE_PENDING,
    CONF_MOON_LOCATION,
    CONF_MOON_MODE,
    CONF_PRESETS,
    CONF_SUN_LOCATION,
    CONF_SUN_MODE,
    CONF_WEATHER_LOCATION,
    DOMAIN,
)
from .api import PrimeError
from .geocode import GeocodeError, geocode
from .library import LibraryError
from .preset import Preset
from .preview import build_preview
from .studio import effective

if TYPE_CHECKING:
    from .coordinator import PrimeConfigEntry

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MODES = vol.In(["anchored", "real"])
_LOCATION = vol.Any(
    None,
    vol.Schema(
        {
            vol.Required("latitude"): vol.All(vol.Coerce(float), cv.latitude),
            vol.Required("longitude"): vol.All(vol.Coerce(float), cv.longitude),
            vol.Optional("name"): str,
        }
    ),
)
_LOCATIONS = vol.Schema(
    {
        vol.Optional(CONF_WEATHER_LOCATION): _LOCATION,
        vol.Optional(CONF_SUN_LOCATION): _LOCATION,
        vol.Optional(CONF_MOON_LOCATION): _LOCATION,
        vol.Optional(CONF_SUN_MODE): _MODES,
        vol.Optional(CONF_MOON_MODE): _MODES,
    }
)

_PERCENT_5 = vol.All(
    vol.Coerce(float), vol.Range(min=0.0, max=5.0), lambda v: round(v, 1)
)

DEFAULT_LIGHT = {CONF_BRIGHTNESS: 100, CONF_NIGHT_LIGHT: 0.0, CONF_MOON_MAX: 3}


def _wire_light(options: dict[str, Any]) -> dict[str, Any]:
    return {key: options.get(key, default) for key, default in DEFAULT_LIGHT.items()}


Handler = Callable[
    [HomeAssistant, websocket_api.ActiveConnection, dict[str, Any], "PrimeConfigEntry"],
    Awaitable[None],
]


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register all commands (call once)."""
    for command in (
        ws_entries,
        ws_config_get,
        ws_locations_set,
        ws_light_set,
        ws_preset_save,
        ws_preset_delete,
        ws_preset_rename,
        ws_preset_activate,
        ws_follow_set,
        ws_preview,
        ws_geocode,
        ws_demo_frame,
        ws_demo_stop,
    ):
        websocket_api.async_register_command(hass, command)


def _loaded_entry(hass: HomeAssistant, entry_id: str) -> PrimeConfigEntry | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        return None
    return entry


def entry_command(func: Handler) -> Callable:
    """Resolve ``entry_id`` and map LibraryError to a websocket error."""

    async def wrapper(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
    ) -> None:
        entry = _loaded_entry(hass, msg["entry_id"])
        if entry is None:
            connection.send_error(msg["id"], "not_found", msg["entry_id"])
            return
        try:
            await func(hass, connection, msg, entry)
        except LibraryError as err:
            connection.send_error(msg["id"], err.code, err.detail)

    wrapper.__name__ = func.__name__
    return websocket_api.async_response(wrapper)


def _title(entry: Any) -> str:
    return entry.title or entry.entry_id


# -- read commands ---------------------------------------------------------
@websocket_api.websocket_command({vol.Required("type"): "ai_prime/entries"})
@callback
def ws_entries(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    connection.send_result(
        msg["id"],
        [
            {
                "entry_id": e.entry_id,
                "title": _title(e),
                "loaded": e.state is ConfigEntryState.LOADED,
            }
            for e in hass.config_entries.async_entries(DOMAIN)
        ],
    )


def _wire_presets(options: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for preset_id, stored in (options.get(CONF_PRESETS) or {}).items():
        try:
            points = library.ramps_to_points(
                {c: [tuple(p) for p in pts] for c, pts in stored["ramps"].items()}
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            continue  # a corrupt entry must not break the whole listing
        result.append(
            {
                "id": preset_id,
                "name": stored.get("name", preset_id),
                "readonly": bool(stored.get("readonly")),
                "points": points,
            }
        )
    return result


def _wire_locations(hass: HomeAssistant, options: dict[str, Any]) -> dict[str, Any]:
    home = {"latitude": hass.config.latitude, "longitude": hass.config.longitude}
    result: dict[str, Any] = {}
    for key in (CONF_WEATHER_LOCATION, CONF_SUN_LOCATION, CONF_MOON_LOCATION):
        result[key] = dict(options.get(key) or home)
    result[CONF_SUN_MODE] = options.get(CONF_SUN_MODE, "anchored")
    result[CONF_MOON_MODE] = options.get(CONF_MOON_MODE, "anchored")
    return result


def _followable(hass: HomeAssistant, entry: PrimeConfigEntry) -> list[dict[str, str]]:
    follows = {
        e.entry_id: e.options.get(CONF_FOLLOW) for e in hass.config_entries.async_entries(DOMAIN)
    }
    result = []
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id == entry.entry_id or other.state is not ConfigEntryState.LOADED:
            continue
        try:
            library.check_follow(entry.entry_id, other.entry_id, follows)
        except LibraryError:
            continue
        result.append({"entry_id": other.entry_id, "title": _title(other)})
    return result


def _effective_active_name(eff: dict[str, Any]) -> str | None:
    entry = (eff.get(CONF_PRESETS) or {}).get(eff.get(CONF_ACTIVE_PRESET))
    return entry.get("name") if entry else None


@websocket_api.websocket_command(
    {vol.Required("type"): "ai_prime/config/get", vol.Required("entry_id"): str}
)
@entry_command
async def ws_config_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    entry: PrimeConfigEntry,
) -> None:
    own = entry.options
    eff = effective(hass, entry)
    connection.send_result(
        msg["id"],
        {
            "presets": _wire_presets(own),
            "active": own.get(CONF_ACTIVE_PRESET),
            "follow": own.get(CONF_FOLLOW),
            "followable": _followable(hass, entry),
            "locations": _wire_locations(hass, own),
            "effective_locations": _wire_locations(hass, eff),
            "effective_active": eff.get(CONF_ACTIVE_PRESET),
            "effective_active_name": _effective_active_name(eff),
            "light": _wire_light(own),
            "lamp_write_pending": bool(own.get(CONF_LAMP_WRITE_PENDING)),
            "home": {"latitude": hass.config.latitude, "longitude": hass.config.longitude},
            "templates": library.templates(own),
        },
    )


# -- write commands --------------------------------------------------------
@websocket_api.websocket_command(
    {
        vol.Required("type"): "ai_prime/locations/set",
        vol.Required("entry_id"): str,
        vol.Required("locations"): _LOCATIONS,
    }
)
@websocket_api.require_admin
@entry_command
async def ws_locations_set(hass, connection, msg, entry) -> None:
    await entry.runtime_data.studio.set_locations(msg["locations"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ai_prime/light/set",
        vol.Required("entry_id"): str,
        vol.Optional(CONF_BRIGHTNESS): vol.All(int, vol.Range(min=1, max=100)),
        vol.Optional(CONF_NIGHT_LIGHT): _PERCENT_5,
        vol.Optional(CONF_MOON_MAX): _PERCENT_5,
    }
)
@websocket_api.require_admin
@entry_command
async def ws_light_set(hass, connection, msg, entry) -> None:
    changes = {k: msg[k] for k in (CONF_BRIGHTNESS, CONF_NIGHT_LIGHT, CONF_MOON_MAX) if k in msg}
    await entry.runtime_data.studio.set_light(changes)
    connection.send_result(msg["id"], _wire_light(entry.options))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ai_prime/preset/save",
        vol.Required("entry_id"): str,
        vol.Optional("preset_id"): str,
        vol.Required("name"): str,
        vol.Required("points"): list,
        vol.Optional("activate", default=False): bool,
    }
)
@websocket_api.require_admin
@entry_command
async def ws_preset_save(hass, connection, msg, entry) -> None:
    ramps = library.points_to_ramps(msg["points"])
    new_id = await entry.runtime_data.studio.save_preset(
        msg.get("preset_id"), msg["name"], ramps, msg["activate"]
    )
    connection.send_result(msg["id"], {"id": new_id})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ai_prime/preset/delete",
        vol.Required("entry_id"): str,
        vol.Required("preset_id"): str,
    }
)
@websocket_api.require_admin
@entry_command
async def ws_preset_delete(hass, connection, msg, entry) -> None:
    await entry.runtime_data.studio.delete_preset(msg["preset_id"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ai_prime/preset/rename",
        vol.Required("entry_id"): str,
        vol.Required("preset_id"): str,
        vol.Required("name"): str,
    }
)
@websocket_api.require_admin
@entry_command
async def ws_preset_rename(hass, connection, msg, entry) -> None:
    await entry.runtime_data.studio.rename_preset(msg["preset_id"], msg["name"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ai_prime/preset/activate",
        vol.Required("entry_id"): str,
        vol.Required("preset_id"): str,
    }
)
@websocket_api.require_admin
@entry_command
async def ws_preset_activate(hass, connection, msg, entry) -> None:
    await entry.runtime_data.studio.activate_preset(msg["preset_id"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ai_prime/follow/set",
        vol.Required("entry_id"): str,
        vol.Required("leader"): vol.Any(None, str),
    }
)
@websocket_api.require_admin
@entry_command
async def ws_follow_set(hass, connection, msg, entry) -> None:
    await entry.runtime_data.studio.set_follow(msg["leader"])
    connection.send_result(msg["id"])


# -- preview / geocode -----------------------------------------------------
@websocket_api.websocket_command(
    {
        vol.Required("type"): "ai_prime/preview",
        vol.Required("entry_id"): str,
        vol.Optional("date"): vol.All(str, vol.Match(_DATE_RE)),
    }
)
@entry_command
async def ws_preview(hass, connection, msg, entry) -> None:
    today = dt_util.now().date()
    if "date" in msg:
        try:
            day = date.fromisoformat(msg["date"])
        except ValueError:
            connection.send_error(msg["id"], "invalid_format", "invalid date")
            return
    else:
        day = today
    runner = entry.runtime_data.nature
    home, sun_loc, sun_mode, moon_loc, moon_mode = runner.locations()
    weather = await runner.current_weather() if day == today else None
    result = await hass.async_add_executor_job(
        build_preview,
        runner.preset,
        day,
        home,
        sun_loc,
        sun_mode,
        moon_loc,
        moon_mode,
        runner.settings,
        weather,
    )
    result["light"] = _wire_light(entry.options)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ai_prime/geocode",
        vol.Required("query"): vol.All(str, vol.Length(max=100)),
    }
)
@websocket_api.async_response
async def ws_geocode(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    language = "de" if (hass.config.language or "").lower().startswith("de") else "en"
    try:
        results = await geocode(async_get_clientsession(hass), msg["query"], language)
    except GeocodeError as err:
        connection.send_error(msg["id"], "geocode_failed", str(err))
        return
    connection.send_result(msg["id"], results)


# -- live demo on the lamp -------------------------------------------------
_DEMO_PERCENT = vol.All(vol.Coerce(float), vol.Range(min=0.0, max=100.0))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ai_prime/demo/frame",
        vol.Required("entry_id"): str,
        vol.Required("values"): vol.Schema({vol.Required(c): _DEMO_PERCENT for c in CHANNELS}),
    }
)
@websocket_api.require_admin
@entry_command
async def ws_demo_frame(hass, connection, msg, entry) -> None:
    demo = entry.runtime_data.demo
    values = {c: round(pct * 10) for c, pct in msg["values"].items()}
    try:
        await demo.frame(values, owner=connection)
    except (PrimeError, HomeAssistantError) as err:
        connection.send_error(msg["id"], "lamp_unreachable", str(err))
        return
    # Stop the demo when the connection that drives it goes away.
    connection.subscriptions[("ai_prime_demo", entry.entry_id)] = demo.stop_for(connection)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {vol.Required("type"): "ai_prime/demo/stop", vol.Required("entry_id"): str}
)
@websocket_api.require_admin
@entry_command
async def ws_demo_stop(hass, connection, msg, entry) -> None:
    await entry.runtime_data.demo.stop()
    connection.send_result(msg["id"])
