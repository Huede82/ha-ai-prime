"""Tests for nature.moon."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.ai_prime.nature import Location
from custom_components.ai_prime.nature.moon import altitude, illumination, moon_window

ZURICH = Location(47.37, 8.54, "Europe/Zurich")
MANAUS = Location(-3.12, -60.02, "America/Manaus")
TZ = ZoneInfo("Europe/Zurich")


def test_moon_illumination_full_and_new():
    assert illumination(datetime(2026, 11, 24, 12, tzinfo=TZ)) > 0.95
    assert illumination(datetime(2026, 11, 9, 12, tzinfo=TZ)) < 0.05


def test_moon_altitude_sanity():
    start = datetime(2026, 11, 18, tzinfo=TZ)
    alts = [altitude(ZURICH, start + timedelta(minutes=10 * i)) for i in range(144)]
    assert all(-90 <= a <= 90 for a in alts)
    up = [a > -0.833 for a in alts]
    transitions = sum(1 for x, y in zip(up, up[1:]) if x != y)
    assert transitions <= 2
    assert any(up) and not all(up)


def test_moon_up_duration_plausible():
    for d in (date(2026, 11, 14), date(2026, 11, 18), date(2026, 11, 24)):
        w = moon_window(ZURICH, d, "real", ZURICH)
        total = sum(b - a for a, b in w) / 60
        assert 8 <= total <= 16, (d, total)


def test_moon_window_real_intervals_sorted_nonoverlapping():
    for d in (date(2026, 11, 10), date(2026, 11, 18), date(2026, 11, 26)):
        w = moon_window(ZURICH, d, "real", ZURICH)
        flat = [x for iv in w for x in iv]
        assert flat == sorted(flat)
        assert all(0 <= a < b <= 1440 for a, b in w)
        assert all(w[i][1] < w[i + 1][0] for i in range(len(w) - 1))


def test_moon_window_anchored_centered_on_midnight():
    w = moon_window(MANAUS, date(2026, 11, 24), "anchored", ZURICH)
    assert len(w) == 2
    (a0, a1), (b0, b1) = w
    assert a0 == 0 and b1 == 1440
    assert abs(a1 - (1440 - b0)) < 1e-6
    assert 9 * 60 / 2 <= a1 <= 15.5 * 60 / 2


def test_moon_window_anchored_moon_down_at_local_midnight():
    d = date(2026, 11, 17)
    midnight = datetime(2026, 11, 17, tzinfo=TZ)
    assert altitude(ZURICH, midnight) < -0.833
    w = moon_window(ZURICH, d, "anchored", ZURICH)
    assert len(w) == 2
    (a0, a1), (b0, b1) = w
    assert a0 == 0 and b1 == 1440
    assert 0 < a1 and abs(a1 - (1440 - b0)) < 1e-6
    assert 8 * 60 / 2 <= a1 <= 15.5 * 60 / 2


def test_moon_window_anchored_continuously_up():
    tromso = Location(69.65, 18.96, "Europe/Oslo")
    d = date(2026, 12, 21)
    assert moon_window(tromso, d, "real", ZURICH) == [(0.0, 1440.0)]
    assert moon_window(tromso, d, "anchored", ZURICH) == [(0.0, 1440.0)]
