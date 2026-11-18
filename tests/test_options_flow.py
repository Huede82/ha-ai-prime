"""Options flow tests."""

from __future__ import annotations

from homeassistant.data_entry_flow import FlowResultType

from custom_components.ai_prime.const import (
    CHANNELS,
    CONF_CLOUD_STRENGTH,
    CONF_LIGHTNING_MAX,
    CONF_LIGHTNING_MIN,
    CONF_MOON_CHANNEL,
    CONF_MOON_LOCATION,
    CONF_MOON_MAX,
    CONF_MOON_MODE,
    CONF_PRESET,
    CONF_SUN_LOCATION,
    CONF_SUN_MODE,
    CONF_WEATHER_LOCATION,
)
from custom_components.ai_prime.preset import Preset, format_preset_text, parse_preset_text

from .conftest import setup_entry

PRESET = Preset(
    name="lamp",
    ramps={c: ((370, 0), (430, 370), (470, 880)) for c in CHANNELS},
).to_dict()


def _active(entry) -> dict:
    stored = entry.options["presets"][entry.options["active_preset"]]
    return {"name": stored["name"], "ramps": stored["ramps"]}


async def _open(hass, stack, options, step):
    entry, _, _ = await setup_entry(hass, stack, options=options)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"locations", "simulation", "preset"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step
    return entry, result


async def test_options_locations_saved_and_preset_kept(hass, stop_patches) -> None:
    entry, result = await _open(
        hass, stop_patches, {CONF_PRESET: PRESET, CONF_CLOUD_STRENGTH: 70}, "locations"
    )
    loc = {"latitude": 47.0, "longitude": 8.0, "radius": 100}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_WEATHER_LOCATION: loc,
            CONF_SUN_LOCATION: loc,
            CONF_MOON_LOCATION: {"latitude": 1.0, "longitude": 2.0},
            CONF_SUN_MODE: "real",
            CONF_MOON_MODE: "anchored",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _active(entry) == PRESET
    assert entry.options[CONF_CLOUD_STRENGTH] == 70
    assert entry.options[CONF_WEATHER_LOCATION] == {"latitude": 47.0, "longitude": 8.0}
    assert entry.options[CONF_MOON_LOCATION] == {"latitude": 1.0, "longitude": 2.0}
    assert entry.options[CONF_SUN_MODE] == "real"
    assert entry.options[CONF_MOON_MODE] == "anchored"


async def test_options_simulation_validation(hass, stop_patches) -> None:
    entry, result = await _open(hass, stop_patches, {CONF_PRESET: PRESET}, "simulation")
    good = {
        CONF_CLOUD_STRENGTH: 40,
        CONF_MOON_MAX: 2.5,
        CONF_MOON_CHANNEL: "green",
        CONF_LIGHTNING_MIN: 30,
        CONF_LIGHTNING_MAX: 90,
    }
    bad = {**good, CONF_LIGHTNING_MIN: 100, CONF_LIGHTNING_MAX: 50}
    result = await hass.config_entries.options.async_configure(result["flow_id"], bad)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "lightning_range"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], good)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_CLOUD_STRENGTH] == 40
    assert entry.options[CONF_MOON_MAX] == 2.5
    assert entry.options[CONF_MOON_CHANNEL] == "green"
    assert entry.options[CONF_LIGHTNING_MIN] == 30
    assert entry.options[CONF_LIGHTNING_MAX] == 90
    assert _active(entry) == PRESET


async def test_options_preset_edit_roundtrip(hass, stop_patches) -> None:
    entry, result = await _open(hass, stop_patches, {CONF_PRESET: PRESET}, "preset")
    keys = {str(k): k for k in result["data_schema"].schema}
    assert set(keys) == {"points"}
    text = keys["points"].default()
    assert text == format_preset_text(Preset.from_dict(PRESET))
    assert "07:10  37  37  37  37  37" in text
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"points": text}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _active(entry) == PRESET

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "preset"}
    )
    edited = "00:00 0 0 0 0 0\n12:00, 50.5 10 0 0 0 # noon\n24:00 0 0 0 0 0"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"points": edited}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    saved = Preset.from_dict(_active(entry))
    assert saved.name == "lamp"
    assert saved.ramps["blue"] == ((0, 0), (720, 505), (1440, 0))
    assert saved.ramps["green"] == ((0, 0), (720, 100), (1440, 0))


async def test_options_preset_invalid_names_line(hass, stop_patches) -> None:
    entry, result = await _open(hass, stop_patches, {CONF_PRESET: PRESET}, "preset")
    bad = "06:10 0 0 0 0 0\n07:10 37 nonsense 37 37 37"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"points": bad}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"points": "invalid_line"}
    assert result["description_placeholders"] == {"line": "2"}
    assert _active(entry) == PRESET
    keys = {str(k): k for k in result["data_schema"].schema}
    assert keys["points"].default() == bad


async def test_options_preset_without_stored_preset(hass, stop_patches) -> None:
    entry, result = await _open(hass, stop_patches, {}, "preset")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"points": "00:00 0 0 0 0 0\n06:00 0 0 0 0 10"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    saved = Preset.from_dict(_active(entry))
    assert saved.name == "custom"
    assert saved.ramps["cool_white"] == ((0, 0), (360, 100))


async def test_preset_step_edits_active(hass, stop_patches) -> None:
    entry, result = await _open(hass, stop_patches, {CONF_PRESET: PRESET}, "preset")
    before = set(entry.options["presets"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"points": "00:00 0 0 0 0 0\n12:00 50 0 0 0 0"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert set(entry.options["presets"]) - {"original"} == before
    assert entry.options["active_preset"] == "natur"
    saved = Preset.from_dict(_active(entry))
    assert saved.name == "lamp"
    assert saved.ramps["blue"] == ((0, 0), (720, 500))


async def test_preset_step_readonly_active_saves_copy(hass, stop_patches) -> None:
    options = {
        "presets": {
            "original": {"name": "Original (Lampe)", "ramps": PRESET["ramps"], "readonly": True}
        },
        "active_preset": "original",
    }
    entry, result = await _open(hass, stop_patches, options, "preset")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"points": "00:00 0 0 0 0 0\n12:00 50 0 0 0 0"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["presets"]["original"]["readonly"] is True
    assert entry.options["presets"]["original"]["ramps"] == PRESET["ramps"]
    active = entry.options["active_preset"]
    assert active != "original"
    assert entry.options["presets"][active]["name"] == "Original (Lampe) (Kopie)"
    assert entry.options["presets"][active]["readonly"] is False


async def test_preset_step_long_readonly_name_copy_fits(hass, stop_patches) -> None:
    long_name = "L" * 58
    options = {
        "presets": {"original": {"name": long_name, "ramps": PRESET["ramps"], "readonly": True}},
        "active_preset": "original",
    }
    entry, result = await _open(hass, stop_patches, options, "preset")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"points": "00:00 0 0 0 0 0\n12:00 50 0 0 0 0"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    name = entry.options["presets"][entry.options["active_preset"]]["name"]
    assert len(name) <= 60
    assert name.endswith(" (Kopie)")


async def test_preset_step_lamp_rejected_shows_error(hass, stop_patches) -> None:
    from unittest.mock import patch

    from custom_components.ai_prime.library import LibraryError

    entry, result = await _open(hass, stop_patches, {"preset": PRESET}, "preset")
    studio = entry.runtime_data.studio
    with patch.object(
        type(studio), "update", side_effect=LibraryError("lamp_rejected", "x")
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"points": "00:00 0 0 0 0 0\n12:00 50 0 0 0 0"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "lamp_rejected"}


async def test_preset_step_too_many_points(hass, stop_patches) -> None:
    entry, result = await _open(hass, stop_patches, {"preset": PRESET}, "preset")
    text = "\n".join(f"{m // 60:02d}:{m % 60:02d} 1 1 1 1 1" for m in range(0, 33 * 30, 30))
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"points": text}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"points": "too_many_points"}
