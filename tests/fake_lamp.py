"""Fake AquaIllumination Prime lamp for tests (mirrors the real local API)."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from aiohttp import web

CHANNELS = ("deep_red", "cool_white", "green", "blue", "warm_white")
POINTS = [(0, 0), (370, 0), (430, 370), (470, 881), (990, 884), (1100, 370), (1220, 0), (1260, 0)]


def make_fake_lamp(password: str | None = None) -> tuple[web.Application, dict[str, Any]]:
    """Return the fake lamp app and its mutable state.

    state["overrides"] maps (method, path) -> (status, body_text, headers) and
    replaces the normal handler; state["fail_schedule_write"] makes POST /api/schedule fail; state["delay"] sleeps before every response.
    """
    state: dict[str, Any] = {
        "colors": {c: 0 for c in CHANNELS},
        "schedule_enabled": True,
        "schedule": {
            "name": "default",
            "ramps": [
                {"color": c, "points": [{"time": t, "intensity": i} for t, i in POINTS]}
                for c in CHANNELS
            ],
        },
        "health": {"celsius": 45.6037, "fan_status": True, "thermal_shutdown_status": False},
        "power_device": {
            "serial_number": "D89760046B9C",
            "type": "Prime Freshwater",
            "max_power": 48000,
            "hd": {"green": 3836, "blue": 3836, "deep_red": 3041, "cool_white": 29096, "warm_white": 20115},
            "normal": {"green": 3073, "blue": 3073, "deep_red": 2436, "cool_white": 23306, "warm_white": 16112},
        },
        "calls": [],
        "overrides": {},
        "delay": 0.0,
        "auth_token": None,  # current prime_auth cookie value; set by POST /login
    }

    def authorized(request: web.Request) -> bool:
        if password is None:
            return True
        # Real firmware: form login sets cookie prime_auth; the API checks it.
        token = state["auth_token"]
        return token is not None and request.cookies.get("prime_auth") == token

    @web.middleware
    async def middleware(request: web.Request, handler):
        state["calls"].append((request.method, request.path))
        if state["delay"]:
            await asyncio.sleep(state["delay"])
        override = state["overrides"].get((request.method, request.path))
        if override is not None:
            status, text, headers = override
            return web.Response(status=status, text=text, headers=headers)
        if request.path not in ("/api/identity", "/login") and not authorized(request):
            return web.json_response({"response_code": 6}, status=401)
        return await handler(request)

    async def login(request):
        form = await request.post()
        if password is not None and form.get("pwd") == password:
            state["auth_token"] = secrets.token_hex(8)
            resp = web.Response(status=303, headers={"Location": "index.html"})
            resp.set_cookie("prime_auth", state["auth_token"])
            return resp
        return web.Response(status=303, headers={"Location": "login.html"})

    async def identity(request):
        return web.json_response(
            {"serial_number": "AI-PRIME-0016", "product": "Prime 16HD",
             "firmware": "2.0.0", "response_code": 0}
        )

    async def get_colors(request):
        return web.json_response({**state["colors"], "response_code": 0})

    async def post_colors(request):
        body = await request.json()
        for c in CHANNELS:
            state["colors"][c] = body[c]
        return web.json_response({"response_code": 0})

    async def put_colors(request):
        return web.json_response({"response_code": 3})

    async def get_enable(request):
        return web.json_response({"enable": state["schedule_enabled"], "response_code": 0})

    async def put_enable(request):
        body = await request.json()
        state["schedule_enabled"] = bool(body["enable"])
        return web.json_response({"response_code": 0})

    async def get_schedule(request):
        return web.json_response({**state["schedule"], "response_code": 0})

    async def post_schedule(request):
        if state.get("fail_schedule_write"):
            return web.json_response({"response_code": 1})
        state["schedule"] = await request.json()
        state["schedule_posts"] = state.get("schedule_posts", 0) + 1
        return web.json_response({"response_code": 0})

    async def get_temperature(request):
        return web.json_response({**state["health"], "response_code": 0})

    async def get_power(request):
        return web.json_response({"devices": [state["power_device"]], "response_code": 0})

    async def not_found(request):
        return web.Response(status=404, text="Error 404: Not Found")

    app = web.Application(middlewares=[middleware])
    app.router.add_post("/login", login)
    app.router.add_get("/api/identity", identity)
    app.router.add_get("/api/colors", get_colors)
    app.router.add_post("/api/colors", post_colors)
    app.router.add_put("/api/colors", put_colors)
    app.router.add_get("/api/schedule/enable", get_enable)
    app.router.add_put("/api/schedule/enable", put_enable)
    app.router.add_get("/api/schedule", get_schedule)
    app.router.add_post("/api/schedule", post_schedule)
    app.router.add_get("/api/temperature", get_temperature)
    app.router.add_get("/api/power", get_power)
    app.router.add_route("*", "/{tail:.*}", not_found)
    return app, state
