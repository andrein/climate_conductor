"""Tests for member visibility (hide_members)."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from custom_components.climate_conductor import _set_member_visibility
from custom_components.climate_conductor.const import CONF_HIDE_MEMBERS, CONF_ROUTES


class _Entry:
    def __init__(self, routes, options=None):
        self.data = {CONF_ROUTES: routes}
        self.options = options or {}


def _register(hass, object_id):
    er.async_get(hass).async_get_or_create(
        "climate", "esphome", f"uid-{object_id}", suggested_object_id=object_id
    )


async def test_hide_members_hides_then_shows(hass):
    """Toggling hidden hides the member entities, and un-toggling shows them."""
    reg = er.async_get(hass)
    _register(hass, "floor")
    _register(hass, "ac")
    entry = _Entry(
        {"heat": "climate.floor", "cool": "climate.ac"}, {CONF_HIDE_MEMBERS: True}
    )

    _set_member_visibility(hass, entry, hidden=True)
    assert reg.async_get("climate.floor").hidden_by == er.RegistryEntryHider.INTEGRATION
    assert reg.async_get("climate.ac").hidden_by == er.RegistryEntryHider.INTEGRATION

    _set_member_visibility(hass, entry, hidden=False)
    assert reg.async_get("climate.floor").hidden_by is None
    assert reg.async_get("climate.ac").hidden_by is None


async def test_visibility_respects_a_manual_user_hide(hass):
    """A member the user hid themselves is left untouched."""
    reg = er.async_get(hass)
    _register(hass, "floor")
    reg.async_update_entity("climate.floor", hidden_by=er.RegistryEntryHider.USER)
    entry = _Entry({"heat": "climate.floor"})

    _set_member_visibility(hass, entry, hidden=False)
    assert reg.async_get("climate.floor").hidden_by == er.RegistryEntryHider.USER
