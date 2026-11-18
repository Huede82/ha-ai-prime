"""Pure-Python sun, moon and weather helpers for nature mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Location:
    """A geographic location with its IANA timezone."""

    latitude: float
    longitude: float
    timezone: str


TimeMode = Literal["anchored", "real"]


def zone_for_longitude(lon: float) -> str:
    """Return a fixed-offset IANA zone (Etc/GMT*) derived from a longitude.

    Etc/ zones have an inverted sign: UTC+4 is "Etc/GMT-4".
    """
    offset = max(-12, min(14, round(lon / 15)))
    return "Etc/GMT" if offset == 0 else f"Etc/GMT{-offset:+d}"
