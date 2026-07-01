"""The Climate Conductor integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Climate Conductor from a config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # via async_on_unload so the listener is dropped on reload; otherwise each
    # reload leaks one and options changes snowball into a reload storm.
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry."""
    # delegate so the entry's async_on_unload callbacks run (dropping the update
    # listener); a direct unload+setup skips them and leaks the listener.
    await hass.config_entries.async_reload(entry.entry_id)
