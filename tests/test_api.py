"""Tests for the AI Prime API client."""

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from custom_components.ai_prime.api import (
    Health,
    PrimeApiError,
    PrimeAuthError,
    PrimeClient,
    PrimeConnectionError,
)
from custom_components.ai_prime.const import CHANNELS

from .fake_lamp import make_fake_lamp


@pytest.fixture
async def lamp(socket_enabled):
    async def _start(password=None):
        app, state = make_fake_lamp(password)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        return server, state

    servers = []
    yield _start
    for s in servers:
        await s.close()


@pytest.fixture
async def session(socket_enabled):
    async with aiohttp.ClientSession() as s:
        yield s


def _client(server, session, **kw):
    return PrimeClient(f"{server.host}:{server.port}", session, **kw)


async def test_colors_returns_channels_only(lamp, session):
    server, state = await lamp()
    state["colors"] = {c: i * 100 for i, c in enumerate(CHANNELS)}
    result = await _client(server, session).colors()
    assert result == state["colors"]
    assert "response_code" not in result


async def test_set_colors_posts_all_channels_and_clamps(lamp, session):
    server, state = await lamp()
    values = {c: 500 for c in CHANNELS} | {"blue": 1500, "green": -5}
    await _client(server, session).set_colors(values)
    assert state["colors"]["blue"] == 1000
    assert state["colors"]["green"] == 0
    assert ("POST", "/api/colors") in state["calls"]


async def test_set_colors_missing_channel_raises(lamp, session):
    server, _ = await lamp()
    with pytest.raises(ValueError):
        await _client(server, session).set_colors({"blue": 1})


async def test_schedule_toggle(lamp, session):
    server, state = await lamp()
    client = _client(server, session)
    await client.set_schedule(False)
    assert await client.schedule_enabled() is False
    assert ("PUT", "/api/schedule/enable") in state["calls"]


async def test_schedule_returns_preset_without_response_code(lamp, session):
    server, state = await lamp()
    result = await _client(server, session).schedule()
    assert "response_code" not in result
    assert result["name"] == "default"


async def test_health_parses(lamp, session):
    server, _ = await lamp()
    assert await _client(server, session).health() == Health(45.6037, True, False)


async def test_nonzero_response_code_raises_api_error(lamp, session):
    server, state = await lamp()
    state["overrides"][("GET", "/api/colors")] = (
        200, '{"response_code": 3}', {"Content-Type": "application/json"}
    )
    with pytest.raises(PrimeApiError) as exc:
        await _client(server, session).colors()
    assert exc.value.code == 3


async def test_auth_required(lamp, session):
    server, _ = await lamp(password="secret")
    with pytest.raises(PrimeAuthError):
        await _client(server, session).colors()
    with pytest.raises(PrimeAuthError):
        await _client(server, session, password="wrong").colors()
    assert set(await _client(server, session, password="secret").colors()) == set(CHANNELS)
    assert (await _client(server, session).identity())["serial_number"]


async def test_login_uses_form_and_cookie(lamp, session):
    server, state = await lamp(password="secret")
    client = _client(server, session, password="secret")
    await client.colors()
    await client.schedule_enabled()
    # One form login, then the cookie is reused for every API call.
    assert [c for c in state["calls"] if c[1] == "/login"] == [("POST", "/login")]


async def test_expired_session_relogs_once(lamp, session):
    server, state = await lamp(password="secret")
    client = _client(server, session, password="secret")
    await client.colors()
    state["auth_token"] = "rotated"  # lamp forgot the session (e.g. reboot)
    assert set(await client.colors()) == set(CHANNELS)
    assert len([c for c in state["calls"] if c[1] == "/login"]) == 2


@pytest.mark.parametrize(
    "override",
    [
        (404, "Error 404: Not Found", {}),
        (303, "", {"Location": "/login.html"}),
        (200, "<html></html>", {"Content-Type": "text/html"}),
        (500, "boom", {}),
    ],
)
async def test_non_json_raises_connection_error(lamp, session, override):
    server, state = await lamp()
    state["overrides"][("GET", "/api/colors")] = override
    with pytest.raises(PrimeConnectionError):
        await _client(server, session).colors()


async def test_timeout_raises_connection_error(lamp, session):
    server, state = await lamp()
    state["delay"] = 0.5
    with pytest.raises(PrimeConnectionError):
        await _client(server, session, timeout=0.1).colors()


async def test_unreachable_raises_connection_error(session):
    with pytest.raises(PrimeConnectionError):
        await PrimeClient("127.0.0.1:1", session, timeout=1).colors()


@pytest.mark.parametrize("body", ['{"response_code": 0}', '{"enable": "yes", "response_code": 0}'])
async def test_schedule_enabled_bad_payload_raises(lamp, session, body):
    server, state = await lamp()
    state["overrides"][("GET", "/api/schedule/enable")] = (
        200, body, {"Content-Type": "application/json"}
    )
    with pytest.raises(PrimeConnectionError):
        await _client(server, session).schedule_enabled()


@pytest.mark.parametrize(
    "body",
    [
        '{"celsius": 40.0, "fan_status": "no", "thermal_shutdown_status": false, "response_code": 0}',
        '{"celsius": 40.0, "fan_status": true, "thermal_shutdown_status": 1, "response_code": 0}',
        '{"celsius": 40.0, "fan_status": null, "thermal_shutdown_status": false, "response_code": 0}',
    ],
)
async def test_health_non_bool_raises(lamp, session, body):
    server, state = await lamp()
    state["overrides"][("GET", "/api/temperature")] = (
        200, body, {"Content-Type": "application/json"}
    )
    with pytest.raises(PrimeConnectionError):
        await _client(server, session).health()
