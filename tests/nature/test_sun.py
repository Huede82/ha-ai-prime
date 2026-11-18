"""Tests for nature.sun."""

from datetime import date

from custom_components.ai_prime.nature import Location
from custom_components.ai_prime.nature.sun import sun_window

ZURICH = Location(47.37, 8.54, "Europe/Zurich")
MANAUS = Location(-3.12, -60.02, "America/Manaus")
TROMSO = Location(69.65, 18.96, "Europe/Oslo")


def test_sun_real_zurich_2026_06_21():
    rise, sset = sun_window(ZURICH, date(2026, 6, 21), "real", ZURICH)
    assert abs(rise - 330) <= 10
    assert abs(sset - 1286) <= 10


def test_sun_anchored_manaus():
    rise, sset = sun_window(MANAUS, date(2026, 12, 21), "anchored", ZURICH)
    assert abs((sset - rise) - 740) <= 10
    assert abs((rise + sset) / 2 - 744) <= 5  # Zurich solar noon Dec 21 ~12:24 CET


def test_sun_anchored_centre_is_home_noon():
    from astral import LocationInfo
    from astral.sun import noon
    from zoneinfo import ZoneInfo

    day = date(2026, 12, 21)
    tz = ZoneInfo(ZURICH.timezone)
    n = noon(LocationInfo("h", "x", ZURICH.timezone, ZURICH.latitude, ZURICH.longitude).observer, day, tzinfo=tz)
    expected = n.hour * 60 + n.minute + n.second / 60
    rise, sset = sun_window(MANAUS, day, "anchored", ZURICH)
    assert abs((rise + sset) / 2 - expected) <= 5


def test_sun_real_manaus_shifted():
    rise, _ = sun_window(MANAUS, date(2026, 12, 21), "real", ZURICH)
    assert abs(rise - 650) <= 20  # ~10:50 Zurich time


def test_sun_polar_none():
    assert sun_window(TROMSO, date(2026, 12, 21), "real", ZURICH) is None
    assert sun_window(TROMSO, date(2026, 12, 21), "anchored", ZURICH) is None
