"""Config flow for Climate Conductor."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, HVACMode
from homeassistant.components.climate.const import ATTR_HVAC_MODES
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN, SensorDeviceClass
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaCommonFlowHandler,
    SchemaConfigFlowHandler,
    SchemaFlowFormStep,
)
import voluptuous as vol

from .const import (
    CONF_HIDE_MEMBERS,
    CONF_MEMBERS,
    CONF_ROUTES,
    CONF_TEMPERATURE_SENSOR,
    DOMAIN,
)

# Prefix for the transient per-mode selector keys in the routing step; stripped
# back off before the routes are persisted.
ROUTE_PREFIX = "route_"

# Canonical display order for routable modes; anything unlisted is appended.
_MODE_ORDER = [
    HVACMode.HEAT,
    HVACMode.COOL,
    HVACMode.HEAT_COOL,
    HVACMode.AUTO,
    HVACMode.DRY,
    HVACMode.FAN_ONLY,
]

BASE_SCHEMA = {
    vol.Required(CONF_MEMBERS): selector.EntitySelector(
        selector.EntitySelectorConfig(domain=CLIMATE_DOMAIN, multiple=True),
    ),
    vol.Optional(CONF_TEMPERATURE_SENSOR): selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain=SENSOR_DOMAIN, device_class=SensorDeviceClass.TEMPERATURE
        ),
    ),
    vol.Required(CONF_HIDE_MEMBERS, default=False): selector.BooleanSelector(),
}


def plan_routes(
    member_modes: Mapping[str, list[str]],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Split members' capabilities into an auto-assigned table and open choices.

    Given ``member -> supported hvac_modes``, return ``(auto, contested)`` where
    ``auto`` maps each mode exactly one member can serve to that member, and
    ``contested`` maps each mode more than one member can serve to the candidate
    list (the only genuine choices the user must make). OFF is never routed.
    """
    capable: dict[str, list[str]] = {}
    for member, modes in member_modes.items():
        for mode in modes:
            if mode == HVACMode.OFF:
                continue
            capable.setdefault(str(mode), []).append(member)

    ordered = [str(m) for m in _MODE_ORDER if str(m) in capable]
    ordered += [m for m in capable if m not in ordered]

    auto: dict[str, str] = {}
    contested: dict[str, list[str]] = {}
    for mode in ordered:
        members = capable[mode]
        if len(members) == 1:
            auto[mode] = members[0]
        else:
            contested[mode] = members
    return auto, contested


def _member_modes(hass: HomeAssistant, members: list[str]) -> dict[str, list[str]]:
    """Read each member's supported hvac_modes from its current state."""
    result: dict[str, list[str]] = {}
    for member in members:
        state = hass.states.get(member)
        result[member] = (
            list(state.attributes.get(ATTR_HVAC_MODES, [])) if state else []
        )
    return result


def _member_label(hass: HomeAssistant, member: str) -> str:
    """Friendly name for a member, falling back to its entity id."""
    state = hass.states.get(member)
    return state.name if state else member


async def _seed_auto_routes(
    handler: SchemaCommonFlowHandler, user_input: dict[str, Any]
) -> dict[str, Any]:
    """After members are picked, seed the routes every member uniquely serves."""
    auto, _ = plan_routes(
        _member_modes(handler.parent_handler.hass, user_input[CONF_MEMBERS])
    )
    return {**user_input, CONF_ROUTES: auto}


def _previous_routes(handler: SchemaCommonFlowHandler) -> dict[str, str]:
    """The routing table already stored on the entry (empty on first setup)."""
    entry = getattr(handler.parent_handler, "config_entry", None)
    if entry is None:
        return {}
    return {**entry.data, **entry.options}.get(CONF_ROUTES, {})


def _route_default(mode: str, candidates: list[str], previous: dict[str, str]) -> str:
    """Pre-select the previously chosen member when it is still a candidate."""
    chosen = previous.get(str(mode))
    return chosen if chosen in candidates else candidates[0]


async def _routes_schema(handler: SchemaCommonFlowHandler) -> vol.Schema | None:
    """Ask only about contested modes; skip the step entirely when none exist."""
    hass = handler.parent_handler.hass
    members = handler.options.get(CONF_MEMBERS, [])
    _, contested = plan_routes(_member_modes(hass, members))
    if not contested:
        return None
    previous = _previous_routes(handler)
    return vol.Schema(
        {
            vol.Required(
                f"{ROUTE_PREFIX}{mode}",
                default=_route_default(mode, candidates, previous),
            ): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=member, label=_member_label(hass, member)
                            )
                            for member in candidates
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            )
            for mode, candidates in contested.items()
        }
    )


async def _merge_contested_routes(
    handler: SchemaCommonFlowHandler, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Fold the user's contested-mode choices into the seeded routing table."""
    chosen = {
        key[len(ROUTE_PREFIX) :]: value
        for key, value in user_input.items()
        if key.startswith(ROUTE_PREFIX)
    }
    routes = {**handler.options.get(CONF_ROUTES, {}), **chosen}
    return {CONF_ROUTES: routes}


CONFIG_FLOW = {
    "user": SchemaFlowFormStep(
        vol.Schema({vol.Required(CONF_NAME): selector.TextSelector()}).extend(
            BASE_SCHEMA
        ),
        validate_user_input=_seed_auto_routes,
        next_step="routes",
    ),
    "routes": SchemaFlowFormStep(
        _routes_schema, validate_user_input=_merge_contested_routes
    ),
}

OPTIONS_FLOW = {
    "init": SchemaFlowFormStep(
        vol.Schema(BASE_SCHEMA),
        validate_user_input=_seed_auto_routes,
        next_step="routes",
    ),
    "routes": SchemaFlowFormStep(
        _routes_schema, validate_user_input=_merge_contested_routes
    ),
}


class ClimateConductorConfigFlow(SchemaConfigFlowHandler, domain=DOMAIN):
    """Config and options flow for Climate Conductor."""

    config_flow = CONFIG_FLOW
    options_flow = OPTIONS_FLOW

    def async_config_entry_title(self, options: Mapping[str, Any]) -> str:
        """Return the config entry title."""
        return cast(str, options.get(CONF_NAME, "Climate Conductor"))
