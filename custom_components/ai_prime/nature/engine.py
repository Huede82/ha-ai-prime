"""Nature mode engine: preset -> sun remap -> clouds -> moon (pure Python)."""

from __future__ import annotations

from dataclasses import dataclass

from ..const import CHANNELS, MAX_VALUE
from ..preset import DAY, DayWindow, Preset
from .weather import Weather


@dataclass(frozen=True)
class NatureSettings:
    """User-tunable engine settings."""

    cloud_strength: float = 0.5
    moon_max: float = 0.03
    moon_channel: str = "blue"
    brightness: float = 1.0
    night_level: int = 0  # lamp units (0.1 %)


@dataclass(frozen=True)
class Target:
    """Channel targets plus the factors that produced them."""

    channels: dict[str, int]
    factors: dict[str, float | bool | None]


def remap_minute(minute: float, preset: DayWindow, sunrise: float, sunset: float) -> float:
    """Map an actual minute to the preset minute whose value should be used.

    Piecewise linear: sunrise -> preset sunrise, sunrise + up -> plateau start,
    sunset - down -> plateau end, sunset -> preset sunset. Ramps keep their
    preset duration; the night is mapped linearly onto the preset's night
    (continuous modulo 1440 across midnight). If the day is
    shorter than both ramps together, the ramps are scaled down and the
    plateau collapses to a single point (the map stays non-decreasing).
    The result may fall outside 0..1440.
    """
    up = preset.plateau_start - preset.sunrise
    down = preset.sunset - preset.plateau_end
    night_p = DAY - (preset.sunset - preset.sunrise)
    night_a = DAY - (sunset - sunrise)
    if night_a > 0:
        ratio = night_p / night_a
        if minute <= sunrise:
            return preset.sunrise - (sunrise - minute) * ratio
        if minute >= sunset:
            return preset.sunset + (minute - sunset) * ratio
    length = sunset - sunrise
    if up + down > length:
        scale = length / (up + down)
        up_a, down_a = up * scale, down * scale
    else:
        up_a, down_a = up, down
    if minute <= sunrise + up_a:
        return preset.sunrise + (minute - sunrise) * up / up_a
    if minute >= sunset - down_a:
        return preset.plateau_end + (minute - (sunset - down_a)) * down / down_a
    span = (sunset - down_a) - (sunrise + up_a)
    frac = (minute - (sunrise + up_a)) / span
    return preset.plateau_start + frac * (preset.plateau_end - preset.plateau_start)


def compute(
    preset: Preset,
    minute: float,
    sun: tuple[float, float] | None,
    moon_up: bool,
    moon_illum: float,
    weather: Weather | None,
    settings: NatureSettings,
) -> Target:
    """Compute the lamp target for one engine tick.

    `sun` is the home-local sunrise/sunset in minutes, or None for polar
    day/night (unmodified preset, `polar` flag set). Moonlight is added on
    `moon_channel` only if every base channel (after remap, before clouds) is
    0 and the moon is up; clouds do not dim moonlight. The constant night light
    (`night_level`) is added on the same channel whenever every base channel is 0.
    Brightness scales the day curve only.
    """
    window = preset.day_window()
    source: float | None = None
    if sun is not None and window is not None:
        source = remap_minute(minute, window, sun[0], sun[1])
        base = preset.value_at(source % DAY)
    else:
        base = preset.value_at(minute)

    cloud = 1.0
    if weather is not None:
        cloud = 1.0 - weather.cloud_cover * settings.cloud_strength
    channels = {c: round(base[c] * settings.brightness * cloud) for c in CHANNELS}

    moon_level = 0
    night_level = 0
    if all(v == 0 for v in base.values()):
        night_level = settings.night_level
        if moon_up:
            moon_level = round(moon_illum * settings.moon_max * 1000)
        channels[settings.moon_channel] = night_level + moon_level

    channels = {c: max(0, min(MAX_VALUE, v)) for c, v in channels.items()}
    return Target(
        channels=channels,
        factors={
            "cloud_factor": cloud,
            "moon_level": moon_level,
            "night_level": night_level,
            "brightness": settings.brightness,
            "polar": sun is None,
            "source_minute": source,
        },
    )
