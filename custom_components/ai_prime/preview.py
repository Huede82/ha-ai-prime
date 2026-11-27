"""Pure preview builder for the studio: sun/moon/weather windows and curves."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun

from .const import CHANNELS
from .nature import Location, TimeMode
from .nature.engine import NatureSettings, compute
from .nature.moon import illumination, moon_window
from .nature.sun import sun_window
from .nature.weather import Weather
from .preset import Preset

THUNDERSTORM_CODE = 95  # Weather keeps no raw code; report a representative one


def _hhmm(moment: datetime) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"


def _local_sun(loc: Location, day: date) -> dict | None:
    """Sunrise/sunset wall-clock times at the sun location (as the runner sees its tz)."""
    tz = ZoneInfo(loc.timezone)
    observer = LocationInfo("loc", "loc", loc.timezone, loc.latitude, loc.longitude).observer
    try:
        times = sun(observer, day, tzinfo=tz)
    except ValueError:
        return None
    return {
        "rise": _hhmm(times["sunrise"]),
        "set": _hhmm(times["sunset"]),
        "tz": loc.timezone,
    }


def _pct(value: float) -> float:
    return round(value / 10, 1)


def build_preview(
    preset: Preset | None,
    day: date,
    home: Location,
    sun_loc: Location,
    sun_mode: TimeMode,
    moon_loc: Location,
    moon_mode: TimeMode,
    settings: NatureSettings,
    weather: Weather | None,
    step: int = 10,
) -> dict:
    """Describe one day: sun/moon windows, weather and base/effective/theory curves."""
    home_tz = ZoneInfo(home.timezone)
    sun_win = sun_window(sun_loc, day, sun_mode, home)
    moon_win = moon_window(moon_loc, day, moon_mode, home)
    noon = datetime.combine(day, time(12), tzinfo=home_tz)

    markers: list[dict] = []
    if sun_win is not None:
        markers.append({"kind": "sunrise", "t": round(sun_win[0])})
        markers.append({"kind": "sunset", "t": round(sun_win[1])})
    for a, b in moon_win:
        if a > 0:
            markers.append({"kind": "moonrise", "t": round(a)})
        if b < 1440:
            markers.append({"kind": "moonset", "t": round(b)})
    markers.sort(key=lambda m: m["t"])

    times = list(range(0, 1440, step)) + [1440]
    base = {c: [] for c in CHANNELS}
    eff = {c: [] for c in CHANNELS}
    theory = {c: [] for c in CHANNELS}
    for t in times:
        if preset is None:
            for c in CHANNELS:
                base[c].append(0)
                eff[c].append(0)
                theory[c].append(0)
            continue
        raw = preset.value_at(t % 1440)
        moon_up = any(a <= t <= b for a, b in moon_win)
        at = datetime.combine(day, time(), tzinfo=home_tz).replace(
            hour=min(t, 1439) // 60, minute=min(t, 1439) % 60
        )
        target = compute(preset, t, sun_win, moon_up, illumination(at), weather, settings)
        ideal = compute(preset, t, sun_win, moon_up, illumination(at), None, settings)
        for c in CHANNELS:
            base[c].append(_pct(raw[c]))
            eff[c].append(_pct(target.channels[c]))
            theory[c].append(_pct(ideal.channels[c]))

    return {
        "date": day.isoformat(),
        "sun": {
            "home": [round(sun_win[0]), round(sun_win[1])] if sun_win else None,
            "local": _local_sun(sun_loc, day),
            "day_length_min": round(sun_win[1] - sun_win[0]) if sun_win else None,
        },
        "moon": {
            "intervals": [[round(a), round(b)] for a, b in moon_win],
            "phase": round(illumination(noon) * 100),
        },
        "weather": (
            {
                "cloud_cover": round(weather.cloud_cover * 100),
                "thunderstorm": weather.thunderstorm,
                "code": THUNDERSTORM_CODE if weather.thunderstorm else 0,
            }
            if weather
            else None
        ),
        "light": {
            "brightness": round(settings.brightness * 100),
            "night_light": _pct(settings.night_level),
            "moon_max": round(settings.moon_max * 100, 1),
        },
        "curve": {"t": times, "base": base, "effective": eff, "theory": theory},
        "markers": markers,
    }
