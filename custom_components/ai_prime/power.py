"""Power model of a lamp (no HA imports)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import CHANNELS


@dataclass(frozen=True)
class PowerModel:
    """Channel power at intensity 1000 in mW (``normal``) and the total budget.

    Matches the vendor app: ``W = sum(value[c] / 1000 * normal[c]) / 1000``.
    """

    max_power: int
    normal: dict[str, int]

    @classmethod
    def from_power(cls, data: Mapping[str, Any]) -> PowerModel:
        """Build from the parsed ``PrimeClient.power()`` dict."""
        return cls(int(data["max_power"]), {c: int(data["normal"][c]) for c in CHANNELS})

    def watts(self, mix: Mapping[str, int]) -> float:
        """Power draw of a mix in W."""
        return sum(mix.get(c, 0) * self.normal[c] for c in CHANNELS) / 1_000_000
