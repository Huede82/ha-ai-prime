"""Fixed-offset zone derived from a longitude."""

import pytest

from custom_components.ai_prime.nature import zone_for_longitude


@pytest.mark.parametrize(
    ("lon", "zone"),
    [
        (-60.02, "Etc/GMT+4"),
        (8.54, "Etc/GMT-1"),
        (174.8, "Etc/GMT-12"),
        (0.3, "Etc/GMT"),
        (-179.0, "Etc/GMT+12"),
        (179.0, "Etc/GMT-12"),
    ],
)
def test_zone_for_longitude(lon: float, zone: str) -> None:
    assert zone_for_longitude(lon) == zone
