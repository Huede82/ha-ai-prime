"""Read-only smoke test against a real AI Prime lamp.

Usage: .venv/bin/python scripts/smoke.py <host> [password]
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent / "custom_components" / "ai_prime"


def _load(name: str):
    """Load an integration module without importing the HA package __init__."""
    spec = importlib.util.spec_from_file_location(f"ai_prime.{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def main(host: str, password: str | None) -> None:
    import types

    sys.modules.setdefault("ai_prime", types.ModuleType("ai_prime")).__path__ = [str(ROOT)]
    _load("const")
    api = _load("api")
    preset = _load("preset")

    async with aiohttp.ClientSession() as session:
        client = api.PrimeClient(host, session, password=password)
        identity = await client.identity()
        print("identity:", identity["product"], identity["serial_number"], identity["firmware"])
        print("colors:  ", await client.colors())
        print("schedule:", await client.schedule_enabled())
        print("health:  ", await client.health())
        p = preset.Preset.from_api(await client.schedule())
        print("preset:  ", p.name, {c: len(r) for c, r in p.ramps.items()})
        print("peak:    ", p.peak_mix())


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
