"""Tests for the schedule preset model."""

from custom_components.ai_prime.const import CHANNELS
import pytest

from custom_components.ai_prime.preset import (
    DayWindow,
    Preset,
    PresetTextError,
    format_preset_text,
    parse_preset_text,
)

from .fake_lamp import POINTS

PAYLOAD = {
    "name": "default",
    "ramps": [
        {"color": c, "points": [{"time": t, "intensity": i} for t, i in POINTS]}
        for c in CHANNELS
    ],
}


def _preset() -> Preset:
    return Preset.from_api(PAYLOAD)


def test_value_at_point():
    assert _preset().value_at(470)["deep_red"] == 881


def test_value_at_interpolates():
    assert _preset().value_at(400)["deep_red"] == 185


def test_value_at_night():
    p = _preset()
    assert all(v == 0 for v in p.value_at(0).values())
    assert all(v == 0 for v in p.value_at(1300).values())


def test_wraparound():
    p = Preset(name="w", ramps={"deep_red": ((60, 100), (1380, 100))})
    assert p.value_at(1430)["deep_red"] == 100


def test_peak_mix():
    p = _preset()
    assert p.peak_mix() == p.value_at(990)


def test_roundtrip():
    p = _preset()
    assert Preset.from_dict(p.to_dict()) == p


def test_missing_empty_single_and_unknown():
    p = Preset.from_api(
        {
            "name": "x",
            "ramps": [
                {"color": "blue", "points": [{"time": 100, "intensity": 500}]},
                {"color": "green", "points": []},
                {"color": "purple", "points": [{"time": 0, "intensity": 9}]},
            ],
        }
    )
    v = p.value_at(700)
    assert list(v) == list(CHANNELS)
    assert v["blue"] == 500
    assert v["green"] == 0 and v["deep_red"] == 0
    assert "purple" not in p.ramps


def test_sorted_and_duplicates_keep_last():
    p = Preset.from_api(
        {
            "name": "x",
            "ramps": [
                {
                    "color": "blue",
                    "points": [
                        {"time": 200, "intensity": 10},
                        {"time": 100, "intensity": 5},
                        {"time": 100, "intensity": 50},
                    ],
                }
            ],
        }
    )
    assert p.ramps["blue"] == ((100, 50), (200, 10))


def test_day_window_16():
    assert _preset().day_window() == DayWindow(370, 470, 990, 1220)


def test_day_window_none_for_constant():
    p = Preset("c", {"blue": ((0, 500), (720, 500))})
    assert p.day_window() is None
    assert Preset("z", {"blue": ((0, 0), (720, 0))}).day_window() is None


TEXT = """
# comment line
06:10  0 0 0 0 0
07:10, 37, 37, 37, 37, 37   # sunrise
07:40  88.5 88 88 88 88
"""


def test_parse_preset_text_ok():
    ramps = parse_preset_text(TEXT)
    assert ramps["blue"] == ((370, 0), (430, 370), (460, 885))
    assert ramps["cool_white"] == ((370, 0), (430, 370), (460, 880))
    assert set(ramps) == set(CHANNELS)


def test_parse_preset_text_column_order_and_sorting():
    ramps = parse_preset_text("12:00 1 2 3 4 5\n00:00 0 0 0 0 0\n")
    assert ramps["blue"] == ((0, 0), (720, 10))
    assert ramps["green"][1] == (720, 20)
    assert ramps["deep_red"][1] == (720, 30)
    assert ramps["warm_white"][1] == (720, 40)
    assert ramps["cool_white"][1] == (720, 50)


@pytest.mark.parametrize(
    ("text", "key", "line"),
    [
        ("00:00 0 0 0 0 0\n06:00 1 2 3", "invalid_line", 2),
        ("00:00 0 0 0 0 0\n06:00 1 2 x 4 5", "invalid_line", 2),
        ("00:00 0 0 0 0 0\n06:00 1.25 2 3 4 5", "invalid_line", 2),
        ("00:00 0 0 0 0 0\n\n25:00 1 1 1 1 1", "invalid_time", 3),
        ("00:00 0 0 0 0 0\n06:60 1 1 1 1 1", "invalid_time", 2),
        ("00:00 0 0 0 0 0\n0600 1 1 1 1 1", "invalid_time", 2),
        ("00:00 0 0 0 0 0\n06:00 101 1 1 1 1", "value_out_of_range", 2),
        ("00:00 0 0 0 0 0\n06:00 -1 1 1 1 1", "invalid_line", 2),
        ("06:00 1 1 1 1 1\n06:00 2 2 2 2 2", "duplicate_time", 2),
        ("06:00 1 1 1 1 1", "too_few_points", None),
        ("# nothing\n", "too_few_points", None),
    ],
)
def test_parse_preset_text_errors(text, key, line):
    with pytest.raises(PresetTextError) as err:
        parse_preset_text(text)
    assert (err.value.key, err.value.line) == (key, line)


def test_parse_preset_text_value_limit_is_100_percent():
    assert parse_preset_text("00:00 0 0 0 0 0\n06:00 100 0 0 0 0")["blue"][1] == (360, 1000)
    with pytest.raises(PresetTextError) as err:
        parse_preset_text("00:00 0 0 0 0 0\n06:00 100.1 0 0 0 0")
    assert (err.value.key, err.value.line) == ("value_out_of_range", 2)


def test_format_preset_text_per_channel_times_is_lossless():
    preset = Preset(
        "lamp",
        {
            "blue": ((360, 0), (720, 500), (1080, 0)),
            "green": ((480, 100), (960, 300)),
            "deep_red": ((360, 0), (1080, 0)),
            "warm_white": ((600, 50), (700, 80)),
            "cool_white": (),
        },
    )
    text = format_preset_text(preset)
    assert text.splitlines()[0].startswith("#")
    assert [ln.split()[0] for ln in text.splitlines()[1:]] == [
        "06:00", "08:00", "10:00", "11:40", "12:00", "16:00", "18:00",
    ]
    back = Preset("lamp", parse_preset_text(text))
    for minute in range(0, 1440, 5):
        for channel in CHANNELS:
            assert abs(back.value_at(minute)[channel] - preset.value_at(minute)[channel]) <= 1
    for channel, points in preset.ramps.items():
        for t, v in points:
            assert back.value_at(t)[channel] == v


def test_format_preset_text_values():
    p = Preset("x", {c: ((370, 0), (430, 370), (990, 885)) for c in CHANNELS})
    assert format_preset_text(p).splitlines()[1:] == [
        "06:10  0  0  0  0  0",
        "07:10  37  37  37  37  37",
        "16:30  88.5  88.5  88.5  88.5  88.5",
    ]
    assert Preset("x", parse_preset_text(format_preset_text(p))).ramps == p.ramps


def test_wraparound_differing_endpoints():
    p = Preset("w", {"blue": ((60, 0), (1380, 200))})
    got = [p.value_at(m)["blue"] for m in (1380, 1410, 0, 30, 60)]
    assert got == [200, 150, 100, 50, 0]
