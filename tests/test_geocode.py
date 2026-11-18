"""Tests for geocode."""

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.ai_prime.geocode import GEOCODE_URL, GeocodeError, geocode


async def test_geocode_maps_results(hass, aioclient_mock):
    aioclient_mock.get(
        GEOCODE_URL,
        json={
            "results": [
                {
                    "name": "Zürich",
                    "country": "Schweiz",
                    "admin1": "Zürich",
                    "latitude": 47.36667123,
                    "longitude": 8.55,
                    "id": 1,
                }
            ]
        },
    )
    out = await geocode(async_get_clientsession(hass), "Zürich", "de")
    assert out == [
        {
            "name": "Zürich",
            "country": "Schweiz",
            "admin1": "Zürich",
            "latitude": 47.3667,
            "longitude": 8.55,
        }
    ]
    assert aioclient_mock.mock_calls[0][1].query["language"] == "de"


async def test_geocode_no_results_key(hass, aioclient_mock):
    aioclient_mock.get(GEOCODE_URL, json={"generationtime_ms": 1})
    assert await geocode(async_get_clientsession(hass), "Nowhere", "en") == []


@pytest.mark.parametrize("query", ["", "a", " b "])
async def test_geocode_short_query(hass, aioclient_mock, query):
    assert await geocode(async_get_clientsession(hass), query, "en") == []
    assert aioclient_mock.call_count == 0


async def test_geocode_http_error(hass, aioclient_mock):
    aioclient_mock.get(GEOCODE_URL, status=500)
    with pytest.raises(GeocodeError):
        await geocode(async_get_clientsession(hass), "Zürich", "en")


async def test_geocode_bad_json(hass, aioclient_mock):
    aioclient_mock.get(GEOCODE_URL, text="nope")
    with pytest.raises(GeocodeError):
        await geocode(async_get_clientsession(hass), "Zürich", "en")
