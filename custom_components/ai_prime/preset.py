"""Schedule preset model: per-channel piecewise-linear ramps over a day."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .const import CHANNELS, MAX_POINTS, MAX_VALUE

DAY = 1440

Point = tuple[int, int]  # (minute 0-1440, value 0-1000)


def _normalize(points: list[Point]) -> tuple[Point, ...]:
    """Sort by time; duplicate times keep the last one given."""
    by_time: dict[int, int] = {}
    for t, v in points:
        by_time[int(t)] = int(v)
    return tuple(sorted(by_time.items()))


def _interpolate(points: tuple[Point, ...], minute: float) -> int:
    if not points:
        return 0
    if len(points) == 1:
        return points[0][1]
    m = minute % DAY
    first_t, first_v = points[0]
    last_t, last_v = points[-1]
    if m >= last_t:
        t0, v0, t1, v1 = last_t, last_v, first_t + DAY, first_v
    elif m < first_t:
        t0, v0, t1, v1 = last_t - DAY, last_v, first_t, first_v
    else:
        t0, v0, t1, v1 = points[0][0], points[0][1], points[0][0], points[0][1]
        for (a_t, a_v), (b_t, b_v) in zip(points, points[1:]):
            if a_t <= m <= b_t:
                t0, v0, t1, v1 = a_t, a_v, b_t, b_v
                break
    if t1 == t0:
        return v1
    value = v0 + (v1 - v0) * (m - t0) / (t1 - t0)
    return max(0, min(MAX_VALUE, round(value)))

PLATEAU_TOLERANCE = 0.98

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_PERCENT_RE = re.compile(r"^\d{1,3}(?:\.\d)?$")


@dataclass(frozen=True)
class DayWindow:
    """Daylight window of a preset, in minutes of the day."""

    sunrise: int
    plateau_start: int
    plateau_end: int
    sunset: int


# Column order of the text editor, as in the vendor app.
EDITOR_ORDER: tuple[str, ...] = ("blue", "green", "deep_red", "warm_white", "cool_white")
EDITOR_HEADER = "# time  blue green deep-red warm-white cool-white (percent)"


class PresetTextError(ValueError):
    """Invalid editor text; ``key`` is the translation key of the error."""

    def __init__(self, key: str, line: int | None = None) -> None:
        super().__init__(f"{key} (line {line})" if line else key)
        self.key = key
        self.line = line


def _format_percent(value: int) -> str:
    return f"{value / 10:.1f}".removesuffix(".0")


def format_preset_text(preset: Preset) -> str:
    """Render a preset as editor text: one line per point time over all channels."""
    minutes = sorted({t for pts in preset.ramps.values() for t, _ in pts})
    lines = [EDITOR_HEADER]
    for m in minutes:
        values = preset.value_at(m)
        cells = "  ".join(_format_percent(values[c]) for c in EDITOR_ORDER)
        lines.append(f"{m // 60:02d}:{m % 60:02d}  {cells}")
    return "\n".join(lines)


def parse_preset_text(text: str) -> dict[str, tuple[Point, ...]]:
    """Parse editor text into per-channel ramps sharing the same time points.

    Raises PresetTextError (key: invalid_line, invalid_time, value_out_of_range,
    duplicate_time, too_few_points) naming the offending line where possible.
    """
    rows: dict[int, dict[str, int]] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tokens = [t for t in re.split(r"[\s,]+", line) if t]
        if len(tokens) != 1 + len(EDITOR_ORDER):
            raise PresetTextError("invalid_line", number)
        time = _TIME_RE.match(tokens[0])
        if time is None:
            raise PresetTextError("invalid_time", number)
        hours, minutes = int(time[1]), int(time[2])
        minute = hours * 60 + minutes
        if minutes > 59 or minute > DAY:
            raise PresetTextError("invalid_time", number)
        values: dict[str, int] = {}
        for channel, token in zip(EDITOR_ORDER, tokens[1:]):
            if _PERCENT_RE.match(token) is None:
                raise PresetTextError("invalid_line", number)
            value = round(float(token) * 10)
            if value > MAX_VALUE:
                raise PresetTextError("value_out_of_range", number)
            values[channel] = value
        if minute in rows:
            raise PresetTextError("duplicate_time", number)
        rows[minute] = values
    if len(rows) < 2:
        raise PresetTextError("too_few_points")
    if len(rows) > MAX_POINTS:
        raise PresetTextError("too_many_points")
    return {
        c: tuple((m, rows[m][c]) for m in sorted(rows)) for c in CHANNELS
    }


@dataclass(frozen=True)
class Preset:
    """A named set of per-channel ramps."""

    name: str
    ramps: dict[str, tuple[Point, ...]]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Preset:
        """Build from an /api/schedule payload; unknown colours are ignored."""
        ramps: dict[str, tuple[Point, ...]] = {}
        for ramp in data.get("ramps", []):
            color = ramp.get("color")
            if color not in CHANNELS:
                continue
            ramps[color] = _normalize(
                [(p["time"], p["intensity"]) for p in ramp.get("points", [])]
            )
        return cls(name=data.get("name", ""), ramps=ramps)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation."""
        return {
            "name": self.name,
            "ramps": {c: [list(p) for p in pts] for c, pts in self.ramps.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Preset:
        """Inverse of to_dict."""
        return cls(
            name=d["name"],
            ramps={
                c: tuple((int(t), int(v)) for t, v in pts)
                for c, pts in d["ramps"].items()
            },
        )

    def value_at(self, minute: float) -> dict[str, int]:
        """Channel values at a minute of the day (wraps around midnight)."""
        return {c: _interpolate(self.ramps.get(c, ()), minute) for c in CHANNELS}

    def peak_mix(self) -> dict[str, int]:
        """Channel values at the defined point minute with the highest sum."""
        minutes = sorted({t for pts in self.ramps.values() for t, _ in pts})
        if not minutes:
            return {c: 0 for c in CHANNELS}
        return max((self.value_at(m) for m in minutes), key=lambda v: sum(v.values()))

    def day_window(self) -> DayWindow | None:
        """Daylight window from the channel sum at the defined points.

        The plateau is every point whose sum is at least 98 % of the maximum
        sum (PLATEAU_TOLERANCE), so tiny differences between per-channel peaks
        do not shorten it. Returns None if the sum never exceeds 0 or never
        returns to 0 on both sides of the plateau.
        """
        minutes = sorted({t for pts in self.ramps.values() for t, _ in pts})
        sums = [(m, sum(self.value_at(m).values())) for m in minutes]
        peak = max((s for _, s in sums), default=0)
        if peak <= 0:
            return None
        first_up = next(i for i, (_, s) in enumerate(sums) if s > 0)
        zeros_before = [m for m, s in sums[:first_up] if s == 0]
        if not zeros_before:
            return None
        plateau = [m for m, s in sums if s >= peak * PLATEAU_TOLERANCE]
        zeros_after = [m for m, s in sums if m > plateau[-1] and s == 0]
        if not zeros_after:
            return None
        return DayWindow(zeros_before[-1], plateau[0], plateau[-1], zeros_after[0])
