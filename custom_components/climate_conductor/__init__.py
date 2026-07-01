"""The Climate Conductor integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_HIDE_MEMBERS, CONF_ROUTES, PLATFORMS


def _set_member_visibility(
    hass: HomeAssistant, entry: ConfigEntry, *, hidden: bool
) -> None:
    """Hide or show the member entities in the registry.

    Only members we hid (or that are shown) are touched, so one the user hid by
    hand is left alone.
    """
    options = {**entry.data, **entry.options}
    members = set(options.get(CONF_ROUTES, {}).values())
    registry = er.async_get(hass)
    target = er.RegistryEntryHider.INTEGRATION if hidden else None
    for member in members:
        existing = registry.async_get(member)
        if existing is None:
            continue
        if existing.hidden_by not in (None, er.RegistryEntryHider.INTEGRATION):
            continue
        if existing.hidden_by != target:
            registry.async_update_entity(member, hidden_by=target)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Climate Conductor from a config entry."""
    hide = {**entry.data, **entry.options}.get(CONF_HIDE_MEMBERS, False)
    _set_member_visibility(hass, entry, hidden=hide)
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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Restore member visibility when the helper is removed."""
    _set_member_visibility(hass, entry, hidden=False)
