"""Tests for the Climate Conductor config flow and route planner."""

from __future__ import annotations

from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResultType

from custom_components.climate_conductor.config_flow import plan_routes
from custom_components.climate_conductor.const import (
    CONF_HIDE_MEMBERS,
    CONF_MEMBERS,
    CONF_ROUTES,
    DOMAIN,
)

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
