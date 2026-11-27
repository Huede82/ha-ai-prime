"""Constants for the AquaIllumination Prime integration."""

from datetime import timedelta

DOMAIN = "ai_prime"

CHANNELS: tuple[str, ...] = (
    "deep_red",
    "cool_white",
    "green",
    "blue",
    "warm_white",
)

MAX_VALUE = 1000
MAX_POINTS = 32  # tested on the lamp; more is untested
SCAN_INTERVAL = timedelta(seconds=30)
CONF_PRESET = "preset"
CONF_PRESETS = "presets"
CONF_ACTIVE_PRESET = "active_preset"
CONF_FOLLOW = "follow"
CONF_LAMP_WRITE_PENDING = "lamp_write_pending"
CONF_ORIGINAL_CAPTURED = "original_captured"

CONF_WEATHER_LOCATION = "weather_location"
CONF_SUN_LOCATION = "sun_location"
CONF_MOON_LOCATION = "moon_location"
CONF_SUN_MODE = "sun_mode"
CONF_MOON_MODE = "moon_mode"
CONF_CLOUD_STRENGTH = "cloud_strength"
CONF_MOON_MAX = "moon_max"
CONF_BRIGHTNESS = "brightness"
CONF_NIGHT_LIGHT = "night_light"
CONF_MOON_CHANNEL = "moon_channel"
CONF_LIGHTNING_MIN = "lightning_min"
CONF_LIGHTNING_MAX = "lightning_max"
