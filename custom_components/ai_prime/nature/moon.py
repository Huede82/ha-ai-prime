"""Moon illumination and low-precision rise/set windows for nature mode.

astral has no moonrise/moonset, so the lunar altitude is computed with a
standard low-precision algorithm (accuracy about 1 degree).
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from astral import moon

from . import Location, TimeMode

# Moon centre altitude threshold: -0.833 deg (standard refraction + semi-diameter).
HORIZON = -0.833
_J2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
_EPS = math.radians(23.4397)
_STEP_MIN = 10


def illumination(at: datetime) -> float:
    """Illuminated fraction 0..1 (astral phase is 0..27.99, 0 = new moon)."""
    phase = moon.phase(at.date())
    return (1 - math.cos(2 * math.pi * phase / 28)) / 2


def altitude(loc: Location, at: datetime) -> float:
    """Geometric altitude of the moon in degrees at ``loc`` and time ``at``."""
    d = (at.astimezone(timezone.utc) - _J2000).total_seconds() / 86400
    mean_lon = math.radians(218.316 + 13.176396 * d)
    anomaly = math.radians(134.963 + 13.064993 * d)
    node_arg = math.radians(93.272 + 13.229350 * d)
    lam = mean_lon + math.radians(6.289) * math.sin(anomaly)
    beta = math.radians(5.128) * math.sin(node_arg)
    ra = math.atan2(
        math.sin(lam) * math.cos(_EPS) - math.tan(beta) * math.sin(_EPS), math.cos(lam)
    )
    dec = math.asin(
        math.sin(beta) * math.cos(_EPS) + math.cos(beta) * math.sin(_EPS) * math.sin(lam)
    )
    sidereal = math.radians(280.16 + 360.9856235 * d + loc.longitude)
    hour_angle = sidereal - ra
    lat = math.radians(loc.latitude)
    return math.degrees(
        math.asin(
            math.sin(lat) * math.sin(dec)
            + math.cos(lat) * math.cos(dec) * math.cos(hour_angle)
        )
    )


def _is_up(loc: Location, at: datetime) -> bool:
    return altitude(loc, at) > HORIZON


def _refine(loc: Location, lo: datetime, hi: datetime) -> datetime:
    """Bisect a horizon crossing between ``lo`` and ``hi`` down to 1 minute."""
    lo_up = _is_up(loc, lo)
    while hi - lo > timedelta(minutes=1):
        mid = lo + (hi - lo) / 2
        if _is_up(loc, mid) == lo_up:
            lo = mid
        else:
            hi = mid
    return lo + (hi - lo) / 2


def _intervals(loc: Location, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Moon-up intervals within [start, end], clipped to the range."""
    result: list[tuple[datetime, datetime]] = []
    t = start
    up = _is_up(loc, t)
    rise = start if up else None
    while t < end:
        nxt = min(t + timedelta(minutes=_STEP_MIN), end)
        nxt_up = _is_up(loc, nxt)
        if nxt_up != up:
            cross = _refine(loc, t, nxt)
            if nxt_up:
                rise = cross
            elif rise is not None:
                result.append((rise, cross))
                rise = None
        t, up = nxt, nxt_up
    if up and rise is not None:
        result.append((rise, end))
    return result


def _local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def moon_window(
    loc: Location, day: date, mode: TimeMode, home: Location
) -> list[tuple[float, float]]:
    """Moon-up intervals in home local minutes (0-1440), sorted, non-overlapping.

    All outputs are home-local wall-clock minutes; DST days are not
    elapsed-time accurate, by design.
    """
    if mode == "real":
        home_tz = ZoneInfo(home.timezone)
        start = _local_midnight(day, home_tz)
        end = _local_midnight(day + timedelta(days=1), home_tz)
        return [
            ((a - start).total_seconds() / 60, (b - start).total_seconds() / 60)
            for a, b in _intervals(loc, start, end)
        ]
    # anchored: the moon-up span around the location's local midnight, centred on home 00:00
    midnight = _local_midnight(day, ZoneInfo(loc.timezone))
    # search wide enough that intervals spanning the +-12 h range are found whole
    lo, hi = midnight - timedelta(hours=36), midnight + timedelta(hours=36)
    found = _intervals(loc, lo, hi)
    near = [
        (a, b)
        for a, b in found
        if b >= midnight - timedelta(hours=12)
        and a <= midnight + timedelta(hours=12)
    ]
    if not near:
        return []
    containing = [iv for iv in near if iv[0] <= midnight <= iv[1]]
    best = containing[0] if containing else min(
        near, key=lambda iv: abs(iv[0] + (iv[1] - iv[0]) / 2 - midnight)
    )
    half = (best[1] - best[0]).total_seconds() / 120
    if half >= 720.0:  # up for 24 h or more (possibly clipped by the search range)
        return [(0.0, 1440.0)]
    return [(0.0, half), (1440.0 - half, 1440.0)]
