"""Tests for nature.engine."""

from datetime import datetime, timezone

import pytest

from custom_components.ai_prime.const import CHANNELS
from custom_components.ai_prime.nature.engine import (
    NatureSettings,
    compute,
    remap_minute,
)
from custom_components.ai_prime.nature.weather import Weather
from custom_components.ai_prime.preset import DayWindow, Preset

from ..fake_lamp import POINTS

PAYLOAD = {
    "name": "default",
    "ramps": [
        {"color": c, "points": [{"time": t, "intensity": i} for t, i in POINTS]}
        for c in CHANNELS
    ],
}
PRESET = Preset.from_api(PAYLOAD)
WIN = DayWindow(370, 470, 990, 1220)
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
SETTINGS = NatureSettings()


def _weather(cloud: float) -> Weather:
    return Weather(cloud_cover=cloud, thunderstorm=False, fetched_at=NOW)


def _compute(minute, sun=None, moon_up=False, illum=0.0, weather=None, settings=SETTINGS):
    return compute(PRESET, minute, sun, moon_up, illum, weather, settings)


@pytest.mark.parametrize("minute", [300, 400, 700, 1100, 1300])
def test_remap_identity(minute):
    assert remap_minute(minute, WIN, 370, 1220) == pytest.approx(minute)


def test_remap_later_sunrise():
    assert remap_minute(450, WIN, 450, 1300) == pytest.approx(370)
    assert remap_minute(550, WIN, 450, 1300) == pytest.approx(470)


def test_remap_short_day_monotonic():
    prev = None
    for m in range(0, 1441):
        r = remap_minute(m, WIN, 600, 700)
        if prev is not None:
            assert r >= prev
        prev = r
    assert remap_minute(600, WIN, 600, 700) == pytest.approx(370)
    assert remap_minute(700, WIN, 600, 700) == pytest.approx(1220)


def test_compute_clouds():
    plain = _compute(990)
    t = _compute(990, weather=_weather(1.0))
    for c in CHANNELS:
        assert plain.channels[c] > 0
        assert t.channels[c] == round(plain.channels[c] * 0.5)
    assert t.channels["blue"] == 442
    assert t.factors["cloud_factor"] == pytest.approx(0.5)


def test_compute_no_weather_factor_one():
    t = _compute(700)
    assert t.factors["cloud_factor"] == 1.0
    assert t.channels == PRESET.value_at(700)


def test_compute_moon_only_at_night():
    night = _compute(60, moon_up=True, illum=1.0)
    assert night.channels["blue"] == 30
    assert all(v == 0 for c, v in night.channels.items() if c != "blue")
    assert night.factors["moon_level"] == 30
    day = _compute(700, moon_up=True, illum=1.0)
    assert day.channels == PRESET.value_at(700)
    assert day.factors["moon_level"] == 0
    down = _compute(60, moon_up=False, illum=1.0)
    assert all(v == 0 for v in down.channels.values())


def test_compute_polar_flag():
    t = _compute(700, sun=None)
    assert t.factors["polar"] is True
    assert t.factors["source_minute"] is None
    assert t.channels == PRESET.value_at(700)
    ok = _compute(700, sun=(370, 1220))
    assert ok.factors["polar"] is False
    assert ok.factors["source_minute"] == pytest.approx(700)


def test_compute_remaps_with_sun():
    t = _compute(550, sun=(450, 1300))
    assert t.factors["source_minute"] == pytest.approx(470)
    assert t.channels == PRESET.value_at(470)


def test_compute_clamps():
    t = _compute(700, moon_up=True, illum=1.0, settings=NatureSettings(moon_max=5.0))
    assert all(0 <= v <= 1000 for v in t.channels.values())
    n = _compute(60, moon_up=True, illum=1.0, settings=NatureSettings(moon_max=5.0))
    assert n.channels["blue"] == 1000


@pytest.mark.parametrize("sun", [(650, 1380), (705, 795)])
@pytest.mark.parametrize("minute", [0, 120, 1380, 1439])
def test_late_sunrise_early_sunset_dark_at_night(sun, minute):
    window = PRESET.day_window()
    if not sun[0] <= minute <= sun[1]:
        target = _compute(minute, sun=sun)
        assert all(v == 0 for v in target.channels.values())
        assert remap_minute(minute, window, *sun) is not None


def test_night_is_dark_for_all_sun_windows():
    window = PRESET.day_window()
    for sunrise in range(200, 901, 50):
        for sunset in range(sunrise + 60, 1441, 60):
            for minute in range(0, 1440):
                if sunrise <= minute <= sunset:
                    continue
                src = remap_minute(minute, window, sunrise, sunset) % 1440
                base = PRESET.value_at(src)
                assert all(v == 0 for v in base.values()), (sunrise, sunset, minute)


def test_remap_continuous_across_midnight():
    window = PRESET.day_window()
    a = remap_minute(1439.999, window, 705, 795) % 1440
    b = remap_minute(0, window, 705, 795) % 1440
    assert a == pytest.approx(b, abs=0.01)
