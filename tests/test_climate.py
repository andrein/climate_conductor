"""Tests for the Climate Conductor climate entity.

Starter tests covering the properties that already have real implementations.
Routing, setpoint forwarding and the member listener get tests as they land.
"""

from __future__ import annotations

from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.climate_conductor.climate import ClimateConductor
from custom_components.climate_conductor.const import (
    CONDUCTOR_CONTEXT_PREFIX,
    CONF_ROUTES,
)


class _FakeEntry:
    """Minimal stand-in for a ConfigEntry (no hass needed for pure props)."""

    def __init__(self, routes, options=None):
        self.entry_id = "test-entry"
        self.title = "Test Room"
        self.data = {CONF_ROUTES: routes}
        self.options = options or {}


# Two self-regulating members: a hydronic floor (heat) and an AC (cool/heat_cool).
_ROUTES = {
    HVACMode.HEAT: "climate.floor",
    HVACMode.COOL: "climate.ac",
    HVACMode.HEAT_COOL: "climate.ac",
}


def _conductor(hass, mode, routes=None):
    """A conductor wired to hass, parked in `mode`, ready for _apply_routing()."""
    ent = ClimateConductor(_FakeEntry(routes or _ROUTES))
    ent.hass = hass
    ent.entity_id = "climate.conductor"  # so async_write_ha_state() can run
    ent._attr_hvac_mode = mode
    return ent


def _setpoints_by_member(calls):
    """Map member entity_id -> the temperature each was commanded to."""
    return {c.data[ATTR_ENTITY_ID]: c.data[ATTR_TEMPERATURE] for c in calls}


def _modes_by_member(calls):
    """Map member entity_id -> the hvac_mode each was commanded to."""
    return {c.data[ATTR_ENTITY_ID]: c.data[ATTR_HVAC_MODE] for c in calls}


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


# --- Routing engine (_apply_routing) --------------------------------------


async def test_routing_drives_active_member_to_the_mode(hass):
    """The member serving the mode is commanded to that mode."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    await _conductor(hass, HVACMode.HEAT)._apply_routing()
    assert _modes_by_member(calls)["climate.floor"] == HVACMode.HEAT


async def test_routing_turns_the_inactive_member_off(hass):
    """Every member that does not serve the mode is turned off (interlock)."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    await _conductor(hass, HVACMode.HEAT)._apply_routing()
    assert _modes_by_member(calls)["climate.ac"] == HVACMode.OFF


async def test_routing_never_has_two_members_active(hass):
    """At most one member is ever driven to a non-off mode."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    await _conductor(hass, HVACMode.COOL)._apply_routing()
    active = [m for m in calls if m.data[ATTR_HVAC_MODE] != HVACMode.OFF]
    assert len(active) == 1
    assert active[0].data[ATTR_ENTITY_ID] == "climate.ac"


async def test_routing_off_turns_every_member_off(hass):
    """OFF routes to no member, so all members are turned off."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    ent = _conductor(hass, HVACMode.OFF)
    await ent._apply_routing()
    assert _modes_by_member(calls) == {
        "climate.floor": HVACMode.OFF,
        "climate.ac": HVACMode.OFF,
    }


async def test_routing_heat_cool_uses_single_member(hass):
    """heat_cool routes wholly to the AC; the floor is turned off."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    await _conductor(hass, HVACMode.HEAT_COOL)._apply_routing()
    assert _modes_by_member(calls) == {
        "climate.ac": HVACMode.HEAT_COOL,
        "climate.floor": HVACMode.OFF,
    }


async def test_routing_commands_carry_echo_suppression_context(hass):
    """Every command is tagged with our context id so the listener drops echoes."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    await _conductor(hass, HVACMode.HEAT)._apply_routing()
    assert calls
    assert all(c.context.id.startswith(CONDUCTOR_CONTEXT_PREFIX) for c in calls)


async def test_routing_commands_one_call_per_member(hass):
    """Exactly one command per member, no duplicates or omissions."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    await _conductor(hass, HVACMode.HEAT)._apply_routing()
    assert sorted(c.data[ATTR_ENTITY_ID] for c in calls) == [
        "climate.ac",
        "climate.floor",
    ]


# --- Setpoint forwarding (async_set_temperature) --------------------------


async def test_set_temperature_forwards_to_active_member(hass):
    """The setpoint is forwarded to the member serving the current mode."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE)
    await _conductor(hass, HVACMode.HEAT).async_set_temperature(temperature=21.5)
    assert _setpoints_by_member(calls) == {"climate.floor": 21.5}


async def test_set_temperature_becomes_authoritative_state(hass):
    """The group stores the setpoint as its own target_temperature."""
    async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE)
    ent = _conductor(hass, HVACMode.COOL)
    await ent.async_set_temperature(temperature=19.0)
    assert ent.target_temperature == 19.0


async def test_set_temperature_forwarding_carries_echo_context(hass):
    """The forwarded setpoint is echo-tagged like every other command."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE)
    await _conductor(hass, HVACMode.HEAT).async_set_temperature(temperature=20.0)
    assert calls
    assert all(c.context.id.startswith(CONDUCTOR_CONTEXT_PREFIX) for c in calls)


async def test_set_temperature_while_off_stores_but_forwards_nothing(hass):
    """With no active member, the setpoint is remembered but sent to no one."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE)
    ent = _conductor(hass, HVACMode.OFF)
    await ent.async_set_temperature(temperature=22.0)
    assert ent.target_temperature == 22.0
    assert calls == []


async def test_routing_forwards_stored_setpoint_to_new_member(hass):
    """Switching mode carries the stored setpoint to the newly active member."""
    async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    temps = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE)
    ent = _conductor(hass, HVACMode.HEAT)
    ent._attr_target_temperature = 23.0
    await ent._apply_routing()
    assert _setpoints_by_member(temps) == {"climate.floor": 23.0}


async def test_supports_target_temperature(hass):
    """The entity advertises target-temperature support."""
    ent = _conductor(hass, HVACMode.HEAT)
    assert ent.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
