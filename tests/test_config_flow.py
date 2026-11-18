"""Config flow tests (client methods patched, no sockets)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ai_prime.api import PrimeAuthError, PrimeConnectionError
from custom_components.ai_prime.const import CONF_PRESET, DOMAIN
from custom_components.ai_prime.preset import Preset

SERIAL = "D89760046B9C"
IDENTITY = {"serial_number": SERIAL, "product": "Prime 16HD", "firmware": "2.0.0"}
SCHEDULE = {
    "name": "default",
    "ramps": [
        {
            "color": c,
            "points": [{"time": 0, "intensity": 0}, {"time": 720, "intensity": 500}],
        }
        for c in ("deep_red", "cool_white", "green", "blue", "warm_white")
    ],
}
COLORS = {"deep_red": 0, "cool_white": 0, "green": 0, "blue": 0, "warm_white": 0}

P = "custom_components.ai_prime.config_flow.PrimeClient"


def _patch_client(**overrides):
    methods = {
        "identity": IDENTITY,
        "colors": COLORS,
        "schedule": SCHEDULE,
    }
    methods.update(overrides)
    patches = []
    for name, value in methods.items():
        kwargs = {"side_effect": value} if isinstance(value, Exception) else {
            "return_value": value
        }
        patches.append(patch(f"{P}.{name}", **kwargs))
    return patches


class _Ctx:
    def __init__(self, **overrides):
        self.patches = _patch_client(**overrides)

    def __enter__(self):
        for p in self.patches:
            p.start()

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow_success(hass: HomeAssistant) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    with _Ctx(), patch("custom_components.ai_prime.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.2.50"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Prime 6B9C"
    assert result["data"] == {CONF_HOST: "192.168.2.50"}
    entry = result["result"]
    assert entry.unique_id == SERIAL
    preset = Preset.from_api(SCHEDULE).to_dict()
    assert entry.version == 2
    assert entry.options["active_preset"] == "natur"
    assert entry.options["presets"]["natur"]["ramps"] == preset["ramps"]
    # New install: the captured lamp schedule is "Lampe", not "Natur".
    assert entry.options["presets"]["natur"]["name"] == "Lampe"


async def test_user_flow_stores_password(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with _Ctx(), patch("custom_components.ai_prime.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.2.50", CONF_PASSWORD: "secret"}
        )
    assert result["data"] == {CONF_HOST: "192.168.2.50", CONF_PASSWORD: "secret"}


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with _Ctx(identity=PrimeConnectionError("down")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.2.50"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with _Ctx(colors=PrimeAuthError("nope")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.2.50", CONF_PASSWORD: "bad"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_duplicate_serial_updates_host(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SERIAL, data={CONF_HOST: "192.168.2.50"}
    )
    entry.add_to_hass(hass)
    result = await _start(hass)
    with _Ctx(), patch(
        "custom_components.ai_prime.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.2.77"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.2.77"


async def test_reauth_updates_password(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        data={CONF_HOST: "192.168.2.50", CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    with _Ctx(), patch(
        "custom_components.ai_prime.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"
    assert entry.data[CONF_HOST] == "192.168.2.50"


async def test_reauth_invalid_auth(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SERIAL, data={CONF_HOST: "192.168.2.50"}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    with _Ctx(colors=PrimeAuthError("nope")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "bad"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_custom_name(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with _Ctx(), patch("custom_components.ai_prime.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.2.50", CONF_NAME: "Reef left"}
        )
    assert result["title"] == "Reef left"
    assert result["data"] == {CONF_HOST: "192.168.2.50"}


async def test_duplicate_serial_before_auth(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        data={CONF_HOST: "192.168.2.50", CONF_PASSWORD: "pw"},
    )
    entry.add_to_hass(hass)
    result = await _start(hass)
    with _Ctx(colors=PrimeAuthError("protected")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.2.77"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.2.77"


async def test_user_flow_unexpected_payload(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with _Ctx(identity={"product": "Prime 16HD"}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.2.50"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_malformed_schedule(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with _Ctx(schedule={"ramps": [{"color": "blue", "points": [{"time": 0}]}]}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.2.50"}
        )
    assert result["errors"] == {"base": "unknown"}


@pytest.mark.parametrize(
    "raw", [" 192.168.2.50 ", "http://192.168.2.50/", "https://192.168.2.50//", "HTTP://192.168.2.50"]
)
async def test_user_flow_normalises_host(hass: HomeAssistant, raw: str) -> None:
    result = await _start(hass)
    with _Ctx(), patch("custom_components.ai_prime.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: raw}
        )
    assert result["data"] == {CONF_HOST: "192.168.2.50"}
