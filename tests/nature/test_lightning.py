"""Tests for the lightning scheduler."""

import random

from custom_components.ai_prime.nature.lightning import LightningScheduler


def test_lightning_pause_in_range():
    sched = LightningScheduler(random.Random(1))
    for _ in range(100):
        assert 20 <= sched.next_pause() <= 120


def test_lightning_pattern_shape():
    sched = LightningScheduler(random.Random(2))
    for _ in range(100):
        pattern = sched.flash_pattern()
        assert 1 <= len(pattern) <= 3
        for on, off in pattern:
            assert on == 0.15
            assert 0.08 <= off <= 0.4
