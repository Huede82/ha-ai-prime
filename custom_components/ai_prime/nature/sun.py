"""Sunrise and sunset windows for nature mode."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import noon, sun

from . import Location, TimeMode


def _observer(loc: Location):
    return LocationInfo("loc", "loc", loc.timezone, loc.latitude, loc.longitude).observer


def _minutes(moment: datetime, day: date, tz: ZoneInfo) -> float:
    """Minutes since local midnight of ``day`` in ``tz``, clamped to 0-1440."""
    midnight = datetime.combine(day, time.min, tzinfo=tz)
    value = (moment.astimezone(tz) - midnight).total_seconds() / 60
    return min(1440.0, max(0.0, value))


def sun_window(
    loc: Location, day: date, mode: TimeMode, home: Location
) -> tuple[float, float] | None:
    """Return (sunrise, sunset) in home local minutes, or None on polar day/night.

    All outputs are home-local wall-clock minutes; DST days are not
    elapsed-time accurate, by design.
    """
    home_tz = ZoneInfo(home.timezone)
    try:
        times = sun(_observer(loc), day, tzinfo=ZoneInfo(loc.timezone))
        if mode == "real":
            return (
                _minutes(times["sunrise"], day, home_tz),
                _minutes(times["sunset"], day, home_tz),
            )
        length = (times["sunset"] - times["sunrise"]).total_seconds() / 60
        centre = _minutes(noon(_observer(home), day, tzinfo=home_tz), day, home_tz)
    except ValueError:
        return None
    half = min(length / 2, centre, 1440.0 - centre)
    return (centre - half, centre + half)
