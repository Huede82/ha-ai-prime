"""Tests for preview.build_preview."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun

from custom_components.ai_prime.const import CHANNELS
from custom_components.ai_prime.nature import Location
from custom_components.ai_prime.nature.engine import NatureSettings
from custom_components.ai_prime.nature.weather import Weather
from custom_components.ai_prime.preset import Preset
from custom_components.ai_prime.preview import build_preview

from .fake_lamp import POINTS

ZH = Location(47.3769, 8.5417, "Europe/Zurich")
DAY = date(2026, 6, 21)
PRESET = Preset.from_api(
    {
        "name": "p",
        "ramps": [
            {"color": c, "points": [{"time": t, "intensity": i} for t, i in POINTS]}
            for c in CHANNELS
        ],
    }
)
SETTINGS = NatureSettings()


def _build(preset=PRESET, weather=None, day=DAY, sun_mode="anchored", moon_mode="anchored", step=10):
    return build_preview(preset, day, ZH, ZH, sun_mode, ZH, moon_mode, SETTINGS, weather, step)


def _min(moment: datetime) -> float:
    return moment.hour * 60 + moment.minute + moment.second / 60


def test_preview_zurich_known_sun_times():
    tz = ZoneInfo("Europe/Zurich")
    ref = sun(LocationInfo("z", "z", "Europe/Zurich", ZH.latitude, ZH.longitude).observer, DAY, tzinfo=tz)
    out = _build(sun_mode="real")
    rise, set_ = out["sun"]["home"]
    assert abs(rise - _min(ref["sunrise"])) <= 5
    assert abs(set_ - _min(ref["sunset"])) <= 5
    assert abs(out["sun"]["day_length_min"] - (_min(ref["sunset"]) - _min(ref["sunrise"]))) <= 5
    assert out["sun"]["local"]["tz"] == "Europe/Zurich"
    assert out["sun"]["local"]["rise"] == f"{ref['sunrise']:%H:%M}"
    assert out["date"] == "2026-06-21"
    assert 0 <= out["moon"]["phase"] <= 100
    # anchored: centred on solar noon, same length
    anch = _build()
    assert abs((anch["sun"]["home"][1] - anch["sun"]["home"][0]) - out["sun"]["day_length_min"]) <= 2


def test_preview_markers_and_curve_lengths():
    out = _build(sun_mode="real", moon_mode="real")
    assert out["curve"]["t"][0] == 0 and out["curve"]["t"][-1] == 1440
    assert len(out["curve"]["t"]) == 145
    for key in ("base", "effective"):
        assert set(out["curve"][key]) == set(CHANNELS)
        assert all(len(v) == 145 for v in out["curve"][key].values())
    kinds = {m["kind"] for m in out["markers"]}
    assert {"sunrise", "sunset"} <= kinds
    assert [m["t"] for m in out["markers"]] == sorted(m["t"] for m in out["markers"])
    assert len(_build(step=60)["curve"]["t"]) == 25


def test_preview_effective_dark_at_night_and_moonlight():
    for d in range(1, 29):
        out = _build(day=date(2026, 6, d), sun_mode="real", moon_mode="real")
        eff = out["curve"]["effective"]
        up = out["moon"]["intervals"]
        for i, t in enumerate(out["curve"]["t"]):
            others = sum(eff[c][i] for c in CHANNELS if c != "blue")
            if t == 0 and not any(a <= t <= b for a, b in up):
                assert eff["blue"][i] == 0 and others == 0  # dark night, no moon
            if any(a <= t <= b for a, b in up) and t in (0, 1440) and eff["blue"][i] > 0:
                assert others == 0  # moonlight is only on the moon channel
        # noon is lit by the sun
        noon = out["curve"]["t"].index(720)
        assert sum(eff[c][noon] for c in CHANNELS) > 0
    # some night sample in the month carries moonlight on blue only
    found = False
    for d in range(1, 29):
        out = _build(day=date(2026, 6, d), sun_mode="real", moon_mode="real")
        eff = out["curve"]["effective"]
        for i, t in enumerate(out["curve"]["t"]):
            if (
                (t <= 180 or t >= 1380)
                and any(a <= t <= b for a, b in out["moon"]["intervals"])
                and eff["blue"][i] > 0
            ):
                assert all(eff[c][i] == 0 for c in CHANNELS if c != "blue")
                found = True
    assert found


def test_preview_clouds_only_when_weather_given():
    cloudy = Weather(cloud_cover=1.0, thunderstorm=True, fetched_at=datetime(2026, 6, 21, tzinfo=timezone.utc))
    plain = _build(sun_mode="real")
    wet = _build(weather=cloudy, sun_mode="real")
    i = plain["curve"]["t"].index(720)
    assert plain["weather"] is None
    assert wet["weather"] == {"cloud_cover": 100, "thunderstorm": True, "code": 95}
    assert sum(wet["curve"]["effective"][c][i] for c in CHANNELS) < sum(
        plain["curve"]["effective"][c][i] for c in CHANNELS
    )
    assert plain["curve"]["base"] == wet["curve"]["base"]


def test_preview_none_preset():
    out = _build(preset=None)
    assert all(v == 0 for ch in ("base", "effective") for vals in out["curve"][ch].values() for v in vals)
    assert out["sun"]["home"] is not None and out["sun"]["local"] is not None
    assert out["curve"]["t"][-1] == 1440
