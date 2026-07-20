"""Tests for the Climate Conductor config flow and route planner."""

from __future__ import annotations

from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.climate_conductor.config_flow import plan_routes
from custom_components.climate_conductor.const import (
    CONF_AREA,
    CONF_HIDE_MEMBERS,
    CONF_MEMBERS,
    CONF_ROUTES,
    DOMAIN,
)


def _area(hass, name="Test Area"):
    """Create an area to satisfy the flow's required area field."""
    return ar.async_get(hass).async_create(name)


# A hydronic floor (heat only) and an AC (cool + heat_cool), the canonical setup.
_FLOOR_AC = {
    "climate.floor": ["off", "heat"],
    "climate.ac": ["off", "cool", "heat_cool"],
}


# --- plan_routes (pure) ----------------------------------------------------


def test_plan_routes_auto_assigns_single_capable_member():
    """A mode only one member can serve is auto-assigned, never prompted."""
    auto, contested = plan_routes(_FLOOR_AC)
    assert auto == {
        "heat": "climate.floor",
        "cool": "climate.ac",
        "heat_cool": "climate.ac",
    }
    assert contested == {}


def test_plan_routes_flags_contested_mode():
    """A mode two members can serve is contested (needs a prompt), not auto."""
    auto, contested = plan_routes(
        {"climate.floor": ["off", "heat"], "climate.ac": ["off", "heat", "cool"]}
    )
    assert auto == {"cool": "climate.ac"}
    assert contested == {"heat": ["climate.floor", "climate.ac"]}


def test_plan_routes_excludes_off():
    """OFF is always available and is never part of the routing table."""
    auto, contested = plan_routes({"climate.ac": ["off"]})
    assert auto == {}
    assert contested == {}


def test_plan_routes_orders_modes_canonically():
    """Modes come out in a stable, sensible order (heat, cool, heat_cool, …)."""
    auto, _ = plan_routes({"climate.ac": ["off", "heat_cool", "cool", "heat"]})
    assert list(auto) == ["heat", "cool", "heat_cool"]


# --- config flow (integration) ---------------------------------------------


async def test_flow_auto_assigns_when_no_contest(hass):
    """With one member per mode, the flow finishes without a routing prompt."""
    hass.states.async_set("climate.floor", "off", {"hvac_modes": ["off", "heat"]})
    hass.states.async_set(
        "climate.ac", "off", {"hvac_modes": ["off", "cool", "heat_cool"]}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Room",
            CONF_AREA: _area(hass).id,
            CONF_MEMBERS: ["climate.floor", "climate.ac"],
            CONF_HIDE_MEMBERS: True,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    entry = result["result"]
    options = {**entry.data, **entry.options}
    assert options[CONF_ROUTES] == {
        "heat": "climate.floor",
        "cool": "climate.ac",
        "heat_cool": "climate.ac",
    }


async def test_flow_prompts_only_for_contested_mode(hass):
    """When two members can serve a mode, the flow asks which one — and only that."""
    hass.states.async_set("climate.floor", "off", {"hvac_modes": ["off", "heat"]})
    hass.states.async_set("climate.ac", "off", {"hvac_modes": ["off", "heat", "cool"]})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Test Room",
            CONF_AREA: _area(hass).id,
            CONF_MEMBERS: ["climate.floor", "climate.ac"],
            CONF_HIDE_MEMBERS: True,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "routes"
    # Only the contested mode (heat) is asked about.
    assert set(result["data_schema"].schema) == {f"route_{HVACMode.HEAT}"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {f"route_{HVACMode.HEAT}": "climate.floor"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    entry = result["result"]
    options = {**entry.data, **entry.options}
    assert options[CONF_ROUTES] == {"cool": "climate.ac", "heat": "climate.floor"}


async def test_flow_defaults_name_to_thermostat(hass):
    """The name field is prefilled with "Thermostat"."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    key = next(k for k in result["data_schema"].schema if str(k) == CONF_NAME)
    assert key.default() == "Thermostat"


async def test_flow_generates_title_entity_id_and_area(hass):
    """Title and entity ID compose from area + name; the entity lands in the area."""
    area = _area(hass, "Mancave")
    hass.states.async_set("climate.floor", "off", {"hvac_modes": ["off", "heat"]})
    hass.states.async_set(
        "climate.ac", "off", {"hvac_modes": ["off", "cool", "heat_cool"]}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Thermostat",
            CONF_AREA: area.id,
            CONF_MEMBERS: ["climate.floor", "climate.ac"],
            CONF_HIDE_MEMBERS: False,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.title == "Mancave Thermostat"
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("climate", DOMAIN, entry.entry_id)
    assert entity_id == "climate.mancave_thermostat"
    assert registry.async_get(entity_id).area_id == area.id
    # display name stays the bare name; the area only prefixes the entity id
    assert hass.states.get(entity_id).attributes["friendly_name"] == "Thermostat"


async def test_reload_respects_user_rename_and_area_move(hass):
    """After creation the registry entry is the user's: reloads change nothing."""
    area = _area(hass, "Mancave")
    hass.states.async_set("climate.floor", "off", {"hvac_modes": ["off", "heat"]})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Thermostat",
            CONF_AREA: area.id,
            CONF_MEMBERS: ["climate.floor"],
            CONF_HIDE_MEMBERS: False,
        },
    )
    entry = result["result"]
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    registry.async_update_entity(
        "climate.mancave_thermostat", new_entity_id="climate.cave", area_id=None
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get_entity_id("climate", DOMAIN, entry.entry_id) == (
        "climate.cave"
    )
    assert registry.async_get("climate.cave").area_id is None


async def test_flow_rejects_members_claimed_by_another_entry(hass):
    """Picking a member another conductor already drives errors; fixing it proceeds."""
    hass.states.async_set("climate.floor", "off", {"hvac_modes": ["off", "heat"]})
    hass.states.async_set("climate.ac", "off", {"hvac_modes": ["off", "cool"]})
    MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_NAME: "Existing Room",
            CONF_MEMBERS: ["climate.ac"],
            CONF_ROUTES: {"cool": "climate.ac"},
        },
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    area = _area(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Duplicate",
            CONF_AREA: area.id,
            CONF_MEMBERS: ["climate.floor", "climate.ac"],
            CONF_HIDE_MEMBERS: False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "member_already_claimed"}

    # dropping the claimed member lets the flow finish
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Duplicate",
            CONF_AREA: area.id,
            CONF_MEMBERS: ["climate.floor"],
            CONF_HIDE_MEMBERS: False,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_options_flow_keeps_own_members_but_rejects_others(hass):
    """Re-saving your own members is fine; claiming another conductor's is not."""
    hass.states.async_set("climate.floor", "off", {"hvac_modes": ["off", "heat"]})
    hass.states.async_set("climate.ac", "off", {"hvac_modes": ["off", "cool"]})
    hass.states.async_set("climate.other", "off", {"hvac_modes": ["off", "cool"]})
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_NAME: "Room",
            CONF_MEMBERS: ["climate.floor"],
            CONF_HIDE_MEMBERS: False,
            CONF_ROUTES: {"heat": "climate.floor"},
        },
    )
    entry.add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_NAME: "Other Room",
            CONF_MEMBERS: ["climate.other"],
            CONF_ROUTES: {"cool": "climate.other"},
        },
    ).add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MEMBERS: ["climate.floor", "climate.other"], CONF_HIDE_MEMBERS: False},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "member_already_claimed"}

    # own members (plus an unclaimed one) pass
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MEMBERS: ["climate.floor", "climate.ac"], CONF_HIDE_MEMBERS: False},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_options_flow_preselects_previous_contested_choice(hass):
    """Reconfiguring defaults a contested mode to the previously chosen member."""
    hass.states.async_set("climate.floor", "off", {"hvac_modes": ["off", "heat"]})
    hass.states.async_set("climate.ac", "off", {"hvac_modes": ["off", "heat", "cool"]})
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_NAME: "Test Room",
            CONF_MEMBERS: ["climate.floor", "climate.ac"],
            CONF_HIDE_MEMBERS: False,
            # both can heat, so heat is contested; the user previously chose the AC
            CONF_ROUTES: {"heat": "climate.ac", "cool": "climate.ac"},
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MEMBERS: ["climate.floor", "climate.ac"], CONF_HIDE_MEMBERS: False},
    )
    assert result["step_id"] == "routes"
    key = next(
        k for k in result["data_schema"].schema if str(k) == f"route_{HVACMode.HEAT}"
    )
    # first candidate is the floor; the default must be the previous choice (AC)
    assert key.default() == "climate.ac"
