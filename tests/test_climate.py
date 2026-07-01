"""Tests for the Climate Conductor climate entity.

Starter tests covering the properties that already have real implementations.
Routing, setpoint forwarding and the member listener get tests as they land.
"""
from __future__ import annotations

from homeassistant.components.climate import HVACMode

from custom_components.climate_conductor.climate import ClimateConductor
from custom_components.climate_conductor.const import (
    CONF_ROUTES,
    CONF_TEMPERATURE_SENSOR,
)


class _FakeEntry:
    """Minimal stand-in for a ConfigEntry (no hass needed for pure props)."""

    def __init__(self, routes, options=None):
        self.entry_id = "test-entry"
        self.title = "Test Room"
        self.data = {CONF_ROUTES: routes}
        self.options = options or {}


def test_advertised_modes_come_from_routes():
    """hvac_modes is OFF + the configured routes, regardless of members."""
    entry = _FakeEntry(
        {
            HVACMode.HEAT: "climate.floor",
            HVACMode.COOL: "climate.ac",
            HVACMode.HEAT_COOL: "climate.ac",
        }
    )
    ent = ClimateConductor(entry)
    assert set(ent.hvac_modes) == {
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.HEAT_COOL,
    }


def test_members_derived_from_routes():
    """Members are the distinct set of routed entities."""
    entry = _FakeEntry({HVACMode.HEAT: "climate.floor", HVACMode.COOL: "climate.ac"})
    ent = ClimateConductor(entry)
    assert ent.members == {"climate.floor", "climate.ac"}


def test_active_member_tracks_selected_mode():
    """The active member is whoever serves the selected mode; None when off."""
    entry = _FakeEntry({HVACMode.HEAT: "climate.floor", HVACMode.COOL: "climate.ac"})
    ent = ClimateConductor(entry)
    assert ent.active_member is None  # starts off
    ent._attr_hvac_mode = HVACMode.HEAT
    assert ent.active_member == "climate.floor"
    ent._attr_hvac_mode = HVACMode.COOL
    assert ent.active_member == "climate.ac"


def test_members_exposed_for_more_info():
    """Member ids are exposed for the more-info member list."""
    from homeassistant.const import ATTR_ENTITY_ID

    entry = _FakeEntry({HVACMode.HEAT: "climate.floor", HVACMode.COOL: "climate.ac"})
    ent = ClimateConductor(entry)
    assert set(ent.extra_state_attributes[ATTR_ENTITY_ID]) == {
        "climate.floor",
        "climate.ac",
    }
