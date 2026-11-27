"""Config flow for the AquaIllumination Prime."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .api import PrimeApiError, PrimeAuthError, PrimeClient, PrimeConnectionError
from .const import (
    CONF_ACTIVE_PRESET,
    CONF_PRESETS,
    CHANNELS,
    CONF_CLOUD_STRENGTH,
    CONF_LIGHTNING_MAX,
    CONF_LIGHTNING_MIN,
    CONF_MOON_CHANNEL,
    CONF_MOON_LOCATION,
    CONF_BRIGHTNESS,
    CONF_NIGHT_LIGHT,
    CONF_MOON_MAX,
    CONF_MOON_MODE,
    CONF_PRESET,
    CONF_SUN_LOCATION,
    CONF_SUN_MODE,
    CONF_WEATHER_LOCATION,
    DOMAIN,
)
from .library import LibraryError, migrate_v1
from .studio import MAX_NAME
from .preset import (
    EDITOR_HEADER,
    Preset,
    PresetTextError,
    format_preset_text,
    parse_preset_text,
)

_LOGGER = logging.getLogger(__name__)

CONF_POINTS = "points"


def _normalize_host(raw: str) -> str:
    """Strip whitespace, an http(s):// scheme and trailing slashes."""
    host = re.sub(r"^https?://", "", raw.strip(), flags=re.IGNORECASE)
    return host.rstrip("/")


_COPY_SUFFIX = " (Kopie)"


def _copy_name(name: str) -> str:
    """Name for an editable copy of a read-only preset, within the name limit."""
    return name.strip()[: MAX_NAME - len(_COPY_SUFFIX)].rstrip() + _COPY_SUFFIX


class PrimeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup and reauth."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PrimeOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _normalize_host(user_input[CONF_HOST])
            password = user_input.get(CONF_PASSWORD) or None
            client = PrimeClient(host, async_get_clientsession(self.hass), password)
            try:
                identity = await client.identity()
                serial = identity["serial_number"]
                # identity is unauthenticated: dedupe before credentials matter.
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                await client.colors()  # verifies credentials
                preset = Preset.from_api(await client.schedule()).to_dict()
            except PrimeAuthError:
                errors["base"] = "invalid_auth"
            except (PrimeConnectionError, PrimeApiError):
                errors["base"] = "cannot_connect"
            except (KeyError, TypeError, ValueError, AttributeError):
                _LOGGER.exception("Unexpected payload from the lamp at %s", host)
                errors["base"] = "unknown"
            else:
                data: dict[str, Any] = {CONF_HOST: host}
                if password:
                    data[CONF_PASSWORD] = password
                name = (user_input.get(CONF_NAME) or "").strip() or f"Prime {serial[-4:]}"
                return self.async_create_entry(
                    title=name, data=data, options=migrate_v1({CONF_PRESET: preset}, name="Lampe")
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str,
                    vol.Optional(CONF_NAME): str,
                    vol.Optional(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            client = PrimeClient(
                entry.data[CONF_HOST], async_get_clientsession(self.hass), password
            )
            try:
                await client.colors()
            except PrimeAuthError:
                errors["base"] = "invalid_auth"
            except (PrimeConnectionError, PrimeApiError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: password}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )


_LOCATION_KEYS = (CONF_WEATHER_LOCATION, CONF_SUN_LOCATION, CONF_MOON_LOCATION)
_MODE_KEYS = (CONF_SUN_MODE, CONF_MOON_MODE)


def _number(minimum: float, maximum: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=NumberSelectorMode.BOX,
        )
    )


class PrimeOptionsFlow(OptionsFlow):
    """Nature-mode options; every step saves and finishes."""

    def _save(self, changes: dict[str, Any]) -> ConfigFlowResult:
        # async_create_entry replaces all options, so merge into the current ones.
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["locations", "simulation", "preset"]
        )

    async def async_step_locations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        opts = self.config_entry.options
        if user_input is not None:
            changes: dict[str, Any] = {key: user_input[key] for key in _MODE_KEYS}
            for key in _LOCATION_KEYS:
                loc = user_input[key]
                changes[key] = {
                    "latitude": loc["latitude"],
                    "longitude": loc["longitude"],
                }
            return self._save(changes)
        home = {
            "latitude": self.hass.config.latitude,
            "longitude": self.hass.config.longitude,
        }
        modes = SelectSelector(
            SelectSelectorConfig(
                options=["anchored", "real"],
                translation_key="time_mode",
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
        schema: dict[Any, Any] = {}
        for key in _LOCATION_KEYS:
            schema[vol.Required(key, default=opts.get(key) or home)] = LocationSelector(
                LocationSelectorConfig(radius=False)
            )
        for key in _MODE_KEYS:
            schema[vol.Required(key, default=opts.get(key, "anchored"))] = modes
        return self.async_show_form(step_id="locations", data_schema=vol.Schema(schema))

    async def async_step_simulation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        opts = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_LIGHTNING_MIN] > user_input[CONF_LIGHTNING_MAX]:
                errors["base"] = "lightning_range"
            else:
                return self._save(dict(user_input))
        defaults = user_input or opts
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CLOUD_STRENGTH, default=defaults.get(CONF_CLOUD_STRENGTH, 50)
                ): _number(0, 100, 1, "%"),
                vol.Required(
                    CONF_MOON_MAX, default=defaults.get(CONF_MOON_MAX, 3)
                ): _number(0, 5, 0.1, "%"),
                vol.Required(
                    CONF_BRIGHTNESS, default=defaults.get(CONF_BRIGHTNESS, 100)
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=100,
                        step=1,
                        unit_of_measurement="%",
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Required(
                    CONF_NIGHT_LIGHT, default=defaults.get(CONF_NIGHT_LIGHT, 0.0)
                ): _number(0, 5, 0.1, "%"),
                vol.Required(
                    CONF_MOON_CHANNEL, default=defaults.get(CONF_MOON_CHANNEL, "blue")
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(CHANNELS),
                        translation_key="channel",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_LIGHTNING_MIN, default=defaults.get(CONF_LIGHTNING_MIN, 20)
                ): _number(5, 600, 1, "s"),
                vol.Required(
                    CONF_LIGHTNING_MAX, default=defaults.get(CONF_LIGHTNING_MAX, 120)
                ): _number(5, 600, 1, "s"),
            }
        )
        return self.async_show_form(
            step_id="simulation", data_schema=schema, errors=errors
        )

    async def async_step_preset(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the active preset as text; a read-only one is saved as a copy."""
        opts = self.config_entry.options
        active_id = opts.get(CONF_ACTIVE_PRESET)
        active = (opts.get(CONF_PRESETS) or {}).get(active_id)
        stored: Preset | None
        try:
            stored = Preset.from_dict({"name": active["name"], "ramps": active["ramps"]})
        except (KeyError, TypeError, ValueError):
            stored = None
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            try:
                ramps = parse_preset_text(user_input[CONF_POINTS])
            except PresetTextError as err:
                errors[CONF_POINTS] = err.key
                placeholders["line"] = str(err.line or "")
            else:
                studio = self.config_entry.runtime_data.studio
                try:
                    if stored is None:
                        await studio.save_preset(None, "custom", ramps, True)
                    elif active.get("readonly"):
                        await studio.save_preset(None, _copy_name(stored.name), ramps, True)
                    else:
                        await studio.save_preset(active_id, stored.name, ramps, True)
                except LibraryError as err:
                    # lamp_rejected: already stored in HA, only the lamp refused it.
                    errors["base"] = err.code
                else:
                    # The studio already stored the options; keep them as they are.
                    return self.async_create_entry(data=dict(self.config_entry.options))
            default = user_input[CONF_POINTS]
        else:
            default = format_preset_text(stored) if stored is not None else EDITOR_HEADER
        return self.async_show_form(
            step_id="preset",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_POINTS, default=default): TextSelector(
                        TextSelectorConfig(multiline=True)
                    )
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )
