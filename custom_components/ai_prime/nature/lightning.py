"""Lightning timing for thunderstorm simulation."""

from __future__ import annotations

import random

FLASH_ON_S = 0.15
FLASH_OFF_MIN_S = 0.08
FLASH_OFF_MAX_S = 0.4


class LightningScheduler:
    """Draws pauses between lightning bursts and the flash pattern of a burst."""

    def __init__(
        self,
        rng: random.Random,
        pause_min_s: float = 20,
        pause_max_s: float = 120,
    ) -> None:
        self._rng = rng
        self._pause_min_s = pause_min_s
        self._pause_max_s = pause_max_s

    def next_pause(self) -> float:
        """Return the pause in seconds until the next burst."""
        return self._rng.uniform(self._pause_min_s, self._pause_max_s)

    def flash_pattern(self) -> list[tuple[float, float]]:
        """Return 1-3 flashes as (on_s, off_s) tuples."""
        return [
            (FLASH_ON_S, self._rng.uniform(FLASH_OFF_MIN_S, FLASH_OFF_MAX_S))
            for _ in range(self._rng.randint(1, 3))
        ]
