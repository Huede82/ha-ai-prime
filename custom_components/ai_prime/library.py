"""Preset library: pure functions over an options mapping (no HA imports)."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .const import (
    CHANNELS,
    CONF_ACTIVE_PRESET,
    CONF_FOLLOW,
    CONF_LAMP_WRITE_PENDING,
    CONF_MOON_LOCATION,
    CONF_MOON_MODE,
    CONF_ORIGINAL_CAPTURED,
    CONF_PRESET,
    CONF_PRESETS,
    CONF_SUN_LOCATION,
    CONF_SUN_MODE,
    CONF_WEATHER_LOCATION,
    MAX_POINTS,
    MAX_VALUE,
)
from .preset import DAY, Point, Preset, parse_preset_text

ORIGINAL_ID = "original"
NATUR_ID = "natur"

LOCATION_KEYS: tuple[str, ...] = (
    CONF_WEATHER_LOCATION,
    CONF_SUN_LOCATION,
    CONF_MOON_LOCATION,
    CONF_SUN_MODE,
    CONF_MOON_MODE,
)

LAMP_MAX_TIME = DAY - 1

# Verbatim copy of examples/natural_preset.txt ("Neu aus Vorlage" -> Natur).
NATUR_TEMPLATE = """# Natural day - columns: Blue Green DeepRed WarmWhite CoolWhite (percent)
06:00   0  0  0  0  0
06:20   4  1  2  0  0   # dawn, faint blue
06:45   6  4 28 20  2   # sunrise, red-orange
07:30  28 24 55 55 22   # morning gold
08:30  80 80 80 85 85   # day (sum constant until 17:00)
12:30  85 85 72 80 88   # noon, slightly cooler
17:00  80 80 80 85 85   # afternoon
18:00  45 40 75 75 45   # golden hour
19:00  12  8 55 35  8   # sunset, red
19:45   8  2 12  4  0   # dusk
20:15   6  0  0  0  0   # blue hour
20:45   0  0  0  0  0
"""


class LibraryError(ValueError):
    """Library operation refused; ``code`` is a stable machine-readable key."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _invalid(detail: str) -> LibraryError:
    return LibraryError("invalid_points", detail)


def points_to_ramps(points: list[dict]) -> dict[str, tuple[Point, ...]]:
    """Wire points ``[{"t": minute, "v": {channel: percent}}]`` -> shared-time ramps."""
    if not isinstance(points, list):
        raise _invalid("points must be a list")
    if len(points) > MAX_POINTS:
        raise _invalid(f"max {MAX_POINTS} points")
    rows: dict[int, dict[str, int]] = {}
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise _invalid(f"point {index}: not an object")
        t = point.get("t")
        if isinstance(t, bool) or not isinstance(t, int) or not 0 <= t <= DAY:
            raise _invalid(f"point {index}: invalid time")
        if t in rows:
            raise _invalid(f"point {index}: duplicate time {t}")
        raw = point.get("v")
        if not isinstance(raw, dict):
            raise _invalid(f"point {index}: missing values")
        values: dict[str, int] = {}
        for channel in CHANNELS:
            v = raw.get(channel)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise _invalid(f"point {index}: invalid value for {channel}")
            if not math.isfinite(v):
                raise _invalid(f"point {index}: invalid value for {channel}")
            scaled = v * 10
            stored = round(scaled)
            if abs(scaled - stored) > 1e-6 or not 0 <= stored <= MAX_VALUE:
                raise _invalid(f"point {index}: invalid value for {channel}")
            values[channel] = stored
        rows[t] = values
    if len(rows) < 2:
        raise _invalid("at least 2 points required")
    return {c: tuple((m, rows[m][c]) for m in sorted(rows)) for c in CHANNELS}


def _value_at(preset: Preset, minute: int) -> dict[str, int]:
    if minute < DAY:
        return preset.value_at(minute)
    # value_at wraps minute 1440 to 0; resolve the end of the day explicitly.
    result = preset.value_at(DAY - 1e-9)
    for channel, pts in preset.ramps.items():
        for t, v in pts:
            if t == DAY:
                result[channel] = v
    return result


def ramps_to_points(ramps: Mapping[str, Iterable[Point]]) -> list[dict]:
    """Shared-time wire points from ramps (union of all channel times)."""
    preset = Preset(
        name="", ramps={c: tuple((int(t), int(v)) for t, v in p) for c, p in ramps.items()}
    )
    minutes = sorted({t for pts in preset.ramps.values() for t, _ in pts})
    points = []
    for m in minutes:
        values = _value_at(preset, m)
        points.append({"t": m, "v": {c: round(values[c] / 10, 1) for c in CHANNELS}})
    return points


def migrate_v1(
    options: Mapping[str, Any], name: str | None = None
) -> dict[str, Any]:
    """Convert single-preset options to the library layout (new dict).

    ``name`` overrides the entry name (new installs: "Lampe"); a real v1
    migration keeps the stored name or falls back to "Natur".
    """
    new = {k: v for k, v in options.items() if k != CONF_PRESET}
    stored = options.get(CONF_PRESET)
    new[CONF_FOLLOW] = None
    new[CONF_LAMP_WRITE_PENDING] = False
    new[CONF_ORIGINAL_CAPTURED] = False
    if not stored:
        new[CONF_PRESETS] = {}
        new[CONF_ACTIVE_PRESET] = None
        return new
    if name is None:
        name = stored.get("name")
        if not name or name in ("default", "custom"):
            name = "Natur"
    new[CONF_PRESETS] = {
        NATUR_ID: {"name": name, "ramps": stored["ramps"], "readonly": False}
    }
    new[CONF_ACTIVE_PRESET] = NATUR_ID
    return new


def new_preset_id(name: str, existing: Iterable[str]) -> str:
    """Slug of ``name`` (``[a-z0-9_]``), suffixed ``_2``, ``_3`` on collision."""
    taken = set(existing)
    slug = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "preset"
    candidate, n = slug, 2
    while candidate in taken:
        candidate = f"{slug}_{n}"
        n += 1
    return candidate


def _presets(options: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return dict(options.get(CONF_PRESETS) or {})


def _entry(presets: Mapping[str, Any], preset_id: str) -> dict[str, Any]:
    if preset_id not in presets:
        raise LibraryError("not_found", preset_id)
    return presets[preset_id]


def save_preset(
    options: Mapping[str, Any],
    preset_id: str | None,
    name: str,
    ramps: Mapping[str, Iterable[Point]],
    activate: bool,
) -> tuple[dict[str, Any], str]:
    """Create (``preset_id`` None) or update an entry; returns (new options, id)."""
    presets = _presets(options)
    if preset_id is None:
        preset_id = new_preset_id(name, presets)
        readonly = False
    else:
        existing = _entry(presets, preset_id)
        if existing.get("readonly"):
            raise LibraryError("readonly", preset_id)
        readonly = False
    presets[preset_id] = {
        "name": name,
        "ramps": {c: [list(p) for p in pts] for c, pts in ramps.items()},
        "readonly": readonly,
    }
    new = {**options, CONF_PRESETS: presets}
    if activate:
        new[CONF_ACTIVE_PRESET] = preset_id
    return new, preset_id


def delete_preset(options: Mapping[str, Any], preset_id: str) -> dict[str, Any]:
    presets = _presets(options)
    entry = _entry(presets, preset_id)
    if options.get(CONF_ACTIVE_PRESET) == preset_id:
        raise LibraryError("in_use", preset_id)
    if entry.get("readonly"):
        raise LibraryError("readonly", preset_id)
    del presets[preset_id]
    return {**options, CONF_PRESETS: presets}


def rename_preset(
    options: Mapping[str, Any], preset_id: str, name: str
) -> dict[str, Any]:
    presets = _presets(options)
    presets[preset_id] = {**_entry(presets, preset_id), "name": name}
    return {**options, CONF_PRESETS: presets}


def activate_preset(options: Mapping[str, Any], preset_id: str) -> dict[str, Any]:
    _entry(_presets(options), preset_id)
    return {**options, CONF_ACTIVE_PRESET: preset_id}


def _active_preset_dict(options: Mapping[str, Any]) -> dict[str, Any] | None:
    presets = options.get(CONF_PRESETS) or {}
    entry = presets.get(options.get(CONF_ACTIVE_PRESET))
    if not entry:
        return None
    try:
        return Preset.from_dict(
            {"name": entry["name"], "ramps": entry["ramps"]}
        ).to_dict()
    except (KeyError, TypeError, ValueError, AttributeError):
        return None  # a corrupt stored preset must not crash every tick


def effective_options(
    own: Mapping[str, Any], leader: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Options a runner uses: the leader's preset and locations if following."""
    effective = dict(own)
    source = own
    if leader is not None:
        source = leader
        effective[CONF_PRESETS] = leader.get(CONF_PRESETS) or {}
        effective[CONF_ACTIVE_PRESET] = leader.get(CONF_ACTIVE_PRESET)
        for key in LOCATION_KEYS:
            effective.pop(key, None)
            if key in leader:
                effective[key] = leader[key]
    effective.pop(CONF_PRESET, None)
    preset = _active_preset_dict(source)
    if preset is not None:
        effective[CONF_PRESET] = preset
    return effective


def check_follow(
    entry_id: str, leader_id: str, follows: Mapping[str, str | None]
) -> None:
    """Refuse self-follow, chains (leader follows someone) and leaders following."""
    if leader_id == entry_id:
        raise LibraryError("follow_cycle", "cannot follow itself")
    if follows.get(leader_id):
        raise LibraryError("follow_cycle", "leader follows another entry")
    if any(f == entry_id for f in follows.values()):
        raise LibraryError("follow_cycle", "entry is a leader of others")


def to_lamp_schedule(preset: Preset) -> dict[str, Any]:
    """Lamp ``/api/schedule`` payload; times clipped to 1439."""
    ramps = []
    for channel in CHANNELS:
        by_time: dict[int, int] = {}
        for t, v in preset.ramps.get(channel, ()):
            by_time[min(t, LAMP_MAX_TIME)] = v
        if not by_time:
            by_time = {0: 0}
        ramps.append(
            {
                "color": channel,
                "points": [
                    {"time": t, "intensity": v} for t, v in sorted(by_time.items())
                ],
            }
        )
    return {"name": preset.name, "ramps": ramps}


def templates(options: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Start points for "new from template": natur, original (if captured), leer."""
    result: list[dict[str, Any]] = [
        {
            "id": NATUR_ID,
            "name": "Natur",
            "points": ramps_to_points(parse_preset_text(NATUR_TEMPLATE)),
        }
    ]
    original = (options.get(CONF_PRESETS) or {}).get(ORIGINAL_ID)
    if original:
        try:
            points = ramps_to_points(
                {c: [tuple(p) for p in pts] for c, pts in original["ramps"].items()}
            )
        except (KeyError, TypeError, ValueError):
            points = []
        if len(points) >= 2:
            result.append({"id": ORIGINAL_ID, "name": "Original", "points": points})
    zero = {c: 0 for c in CHANNELS}
    result.append(
        {
            "id": "leer",
            "name": "Leer",
            "points": [{"t": 0, "v": dict(zero)}, {"t": LAMP_MAX_TIME, "v": dict(zero)}],
        }
    )
    return result
