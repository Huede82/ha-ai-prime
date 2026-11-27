"""Studio store: preset library and follow resolution over a config entry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from . import library
from .const import CONF_ACTIVE_PRESET, CONF_BRIGHTNESS, CONF_FOLLOW, DOMAIN
from .library import LOCATION_KEYS, LibraryError
from .preset import Point

if TYPE_CHECKING:
    from .coordinator import PrimeConfigEntry


def leader_entry(hass: HomeAssistant, entry: PrimeConfigEntry) -> PrimeConfigEntry | None:
    """The loaded entry ``entry`` follows, or None."""
    leader_id = entry.options.get(CONF_FOLLOW)
    if not leader_id or leader_id == entry.entry_id:
        return None
    leader = hass.config_entries.async_get_entry(leader_id)
    if leader is None or leader.domain != DOMAIN or leader.state is not ConfigEntryState.LOADED:
        return None
    return leader


def effective(hass: HomeAssistant, entry: PrimeConfigEntry) -> dict[str, Any]:
    """Options the runner uses; the leader's preset/locations when following."""
    leader = leader_entry(hass, entry)
    return library.effective_options(
        entry.options, leader.options if leader is not None else None
    )


def followers(hass: HomeAssistant, entry: PrimeConfigEntry) -> list[PrimeConfigEntry]:
    """Loaded entries that follow ``entry``."""
    return [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
        and other.state is ConfigEntryState.LOADED
        and other.options.get(CONF_FOLLOW) == entry.entry_id
    ]


MAX_NAME = 60


def _clean_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise LibraryError("invalid_name", "name must not be empty")
    if len(name.strip()) > MAX_NAME:
        raise LibraryError("invalid_name", f"max {MAX_NAME} characters")
    return name.strip()


class Studio:
    """Per-entry preset library; the only writer of library options."""

    def __init__(self, hass: HomeAssistant, entry: PrimeConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

    async def update(self, new_options: dict[str, Any], *, lamp_write: bool) -> None:
        """Store options and refresh this entry and its followers.

        Raises ``lamp_rejected`` after everything is stored and refreshed when a
        lamp refused the schedule (the options change itself is kept).
        """
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        rejected: list[str] = []
        for entry in (self.entry, *followers(self.hass, self.entry)):
            if await entry.runtime_data.studio.refresh(lamp_write=lamp_write) == "rejected":
                rejected.append(entry.title or entry.entry_id)
        if rejected:
            raise LibraryError("lamp_rejected", ", ".join(rejected))

    async def refresh(self, *, lamp_write: bool) -> str | None:
        """Re-read effective options (no options change); optionally write the lamp.

        Returns the lamp write outcome (``LampWrite``) or None without a write.
        """
        coordinator = self.entry.runtime_data
        if coordinator.nature is not None:
            await coordinator.nature.apply_options()
        if lamp_write:
            return await coordinator.write_active_schedule()
        return None

    # -- wrappers used by the websocket API ------------------------------
    async def save_preset(
        self,
        preset_id: str | None,
        name: str,
        ramps: Mapping[str, Iterable[Point]],
        activate: bool,
        *,
        lamp_write: bool | None = None,
    ) -> str:
        """Save a preset; ``ramps`` must come from points_to_ramps/parse_preset_text."""
        name = _clean_name(name)
        options = self.entry.options
        new, new_id = library.save_preset(options, preset_id, name, ramps, activate)
        if lamp_write is None:
            lamp_write = new.get(CONF_ACTIVE_PRESET) == new_id
        await self.update(new, lamp_write=lamp_write)
        return new_id

    async def delete_preset(self, preset_id: str) -> None:
        await self.update(
            library.delete_preset(self.entry.options, preset_id), lamp_write=False
        )

    async def rename_preset(self, preset_id: str, name: str) -> None:
        name = _clean_name(name)
        await self.update(
            library.rename_preset(self.entry.options, preset_id, name), lamp_write=False
        )

    async def activate_preset(self, preset_id: str) -> None:
        await self.update(
            library.activate_preset(self.entry.options, preset_id), lamp_write=True
        )

    async def set_locations(self, locations: Mapping[str, Any]) -> None:
        unknown = set(locations) - set(LOCATION_KEYS)
        if unknown:
            raise LibraryError("invalid_locations", ", ".join(sorted(unknown)))
        await self.update({**self.entry.options, **locations}, lamp_write=False)

    async def set_light(self, changes: Mapping[str, Any]) -> None:
        """Store light settings (per entry, never inherited); re-write the lamp on a brightness change."""
        options = self.entry.options
        write = CONF_BRIGHTNESS in changes and changes[CONF_BRIGHTNESS] != options.get(
            CONF_BRIGHTNESS, 100
        )
        self.hass.config_entries.async_update_entry(
            self.entry, options={**options, **changes}
        )
        if await self.refresh(lamp_write=write) == "rejected":
            raise LibraryError("lamp_rejected", self.entry.title or self.entry.entry_id)

    async def set_follow(self, leader_id: str | None) -> None:
        if leader_id is not None:
            other = self.hass.config_entries.async_get_entry(leader_id)
            if other is None or other.domain != DOMAIN:
                raise LibraryError("not_found", leader_id)
            follows = {
                e.entry_id: e.options.get(CONF_FOLLOW)
                for e in self.hass.config_entries.async_entries(DOMAIN)
            }
            library.check_follow(self.entry.entry_id, leader_id, follows)
        await self.update({**self.entry.options, CONF_FOLLOW: leader_id}, lamp_write=True)

