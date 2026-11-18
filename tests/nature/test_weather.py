"""Tests for the Open-Meteo weather source."""

from datetime import datetime, timedelta, timezone

import asyncio

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.ai_prime.nature import Location
from custom_components.ai_prime.nature.weather import Weather, WeatherSource

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
LOC = Location(47.3769, 8.5417, "Europe/Zurich")


class FakeMeteo:
    def __init__(self):
        self.calls = []
        self.status = 200
        self.body = {"current": {"cloud_cover": 75, "weather_code": 95}}
        self.raw = None

    async def handle(self, request):
        self.calls.append(dict(request.query))
        if self.raw is not None:
            return web.Response(text=self.raw, status=self.status)
        return web.json_response(self.body, status=self.status)


@pytest.fixture
async def meteo(socket_enabled):
    fake = FakeMeteo()
    app = web.Application()
    app.router.add_get("/v1/forecast", fake.handle)
    server = TestServer(app)
    await server.start_server()
    fake.url = str(server.make_url("/v1/forecast"))
    yield fake
    await server.close()


@pytest.fixture
async def session(socket_enabled):
    async with aiohttp.ClientSession() as s:
        yield s


def _source(meteo, session, loc=lambda: LOC):
    return WeatherSource(session, loc, url=meteo.url)


async def test_weather_parses_open_meteo(meteo, session):
    w = await _source(meteo, session).get(NOW)
    assert w == Weather(0.75, True, NOW)
    assert meteo.calls == [
        {"latitude": "47.38", "longitude": "8.54", "current": "cloud_cover,weather_code"}
    ]


async def test_weather_cached_within_refresh(meteo, session):
    src = _source(meteo, session)
    await src.get(NOW)
    w = await src.get(NOW + timedelta(minutes=5))
    assert len(meteo.calls) == 1
    assert w.fetched_at == NOW
    await src.get(NOW + timedelta(minutes=16))
    assert len(meteo.calls) == 2


async def test_weather_keeps_last_value_when_down(meteo, session, caplog):
    src = _source(meteo, session)
    first = await src.get(NOW)
    meteo.status = 500
    caplog.set_level("INFO")
    w = await src.get(NOW + timedelta(minutes=20))
    assert w == first
    w = await src.get(NOW + timedelta(minutes=22))
    assert len(meteo.calls) == 2  # throttled: no retry within 5 min of failure
    w = await src.get(NOW + timedelta(minutes=40))
    assert w == first
    assert len(meteo.calls) == 3
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    meteo.status = 200
    w = await src.get(NOW + timedelta(minutes=60))
    assert w.fetched_at == NOW + timedelta(minutes=60)
    assert any(r.levelname == "INFO" for r in caplog.records)


async def test_weather_resets_after_stale(meteo, session):
    src = _source(meteo, session)
    await src.get(NOW)
    meteo.status = 500
    later = NOW + timedelta(hours=1, minutes=1)
    assert await src.get(later) == Weather(0.0, False, later)


async def test_weather_no_value_and_down_returns_clear(meteo, session):
    meteo.status = 500
    assert await _source(meteo, session).get(NOW) == Weather(0.0, False, NOW)


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        '{"nope": 1}',
        '{"current": {"cloud_cover": "x", "weather_code": 1}}',
        '{"current": {"cloud_cover": 50}}',
        '{"current": []}',
        "[1]",
        '{"current": {"cloud_cover": Infinity, "weather_code": 1}}',
        '{"current": {"cloud_cover": NaN, "weather_code": 1}}',
        '{"current": {"cloud_cover": 50, "weather_code": Infinity}}',
        '{"current": {"cloud_cover": 50, "weather_code": NaN}}',
        '{"current": {"cloud_cover": 50, "weather_code": true}}',
        '{"current": {"cloud_cover": 50, "weather_code": 95.5}}',
    ],
)
async def test_weather_garbage_payload_treated_as_error(meteo, session, raw):
    src = _source(meteo, session)
    first = await src.get(NOW)
    meteo.raw = raw
    w = await src.get(NOW + timedelta(minutes=20))
    assert w == first


async def test_weather_clamps_cloud_cover(meteo, session):
    meteo.body = {"current": {"cloud_cover": 140, "weather_code": 3}}
    w = await _source(meteo, session).get(NOW)
    assert w == Weather(1.0, False, NOW)


async def test_weather_refetch_on_location_change(meteo, session):
    loc = [LOC]
    src = _source(meteo, session, lambda: loc[0])
    await src.get(NOW)
    loc[0] = Location(47.3779, 8.5427, "Europe/Zurich")  # same after rounding
    await src.get(NOW + timedelta(minutes=1))
    assert len(meteo.calls) == 1
    loc[0] = Location(48.2, 16.37, "Europe/Vienna")
    await src.get(NOW + timedelta(minutes=2))
    assert len(meteo.calls) == 2
    assert meteo.calls[1]["latitude"] == "48.2"


async def test_weather_concurrent_gets_single_request(meteo, session):
    src = _source(meteo, session)
    a, b = await asyncio.gather(src.get(NOW), src.get(NOW))
    assert a == b
    assert len(meteo.calls) == 1


async def test_weather_provider_error_does_not_raise(meteo, session):
    def boom():
        raise RuntimeError("no location")

    w = await _source(meteo, session, boom).get(NOW)
    assert w == Weather(0.0, False, NOW)
    assert meteo.calls == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
async def test_weather_non_finite_coordinates_are_provider_failure(meteo, session, bad):
    src = _source(meteo, session, lambda: Location(bad, 8.5, "UTC"))
    w = await src.get(NOW)
    assert w == Weather(0.0, False, NOW)
    assert meteo.calls == []


async def test_weather_available_tracks_freshness(meteo, session):
    src = _source(meteo, session)
    assert not src.available
    await src.get(NOW)
    assert src.available
    meteo.status = 500
    await src.get(NOW + timedelta(minutes=40))
    assert src.available  # still within stale_after
    await src.get(NOW + timedelta(hours=1, minutes=1))
    assert not src.available
