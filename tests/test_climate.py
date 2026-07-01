"""Tests for the Climate Conductor climate entity.

Starter tests covering the properties that already have real implementations.
Routing, setpoint forwarding and the member listener get tests as they land.
"""

from __future__ import annotations

from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.components.climate.const import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    ATTR_SWING_MODE,
    ATTR_SWING_MODES,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_SWING_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
)
from homeassistant.core import Context, Event, State
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.climate_conductor.climate import ClimateConductor
from custom_components.climate_conductor.const import (
    CONDUCTOR_CONTEXT_PREFIX,
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


async def test_routing_adopts_active_member_setpoint(hass):
    """Routing takes the new active member's own setpoint as authoritative."""
    async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    hass.states.async_set("climate.floor", "heat", {ATTR_TEMPERATURE: 20.0})
    ent = _conductor(hass, HVACMode.HEAT)
    ent._attr_target_temperature = 23.0  # a stale value from another mode
    await ent._apply_routing()
    assert ent.target_temperature == 20.0


async def test_routing_does_not_forward_across_modes(hass):
    """Routing never pushes the old setpoint onto the new member (no bleed)."""
    async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    temps = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE)
    hass.states.async_set("climate.floor", "heat", {ATTR_TEMPERATURE: 20.0})
    ent = _conductor(hass, HVACMode.HEAT)
    ent._attr_target_temperature = 23.0
    await ent._apply_routing()
    assert temps == []


async def test_supports_target_temperature(hass):
    """The entity advertises target-temperature support."""
    ent = _conductor(hass, HVACMode.HEAT)
    assert ent.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE


# --- Mirroring the active member for display -------------------------------


async def test_current_temperature_mirrors_active_member(hass):
    """current_temperature comes from the member serving the mode."""
    hass.states.async_set("climate.floor", "heat", {ATTR_CURRENT_TEMPERATURE: 19.5})
    assert _conductor(hass, HVACMode.HEAT).current_temperature == 19.5


async def test_current_temperature_prefers_override_sensor(hass):
    """A configured temperature-sensor override wins over the member reading."""
    hass.states.async_set("climate.floor", "heat", {ATTR_CURRENT_TEMPERATURE: 19.5})
    hass.states.async_set("sensor.room", "21.2")
    ent = ClimateConductor(
        _FakeEntry(_ROUTES, {CONF_TEMPERATURE_SENSOR: "sensor.room"})
    )
    ent.hass = hass
    ent.entity_id = "climate.conductor"
    ent._attr_hvac_mode = HVACMode.HEAT
    assert ent.current_temperature == 21.2


async def test_current_temperature_none_when_off(hass):
    """With no active member there is no reading to mirror."""
    assert _conductor(hass, HVACMode.OFF).current_temperature is None


async def test_target_temperature_falls_back_to_active_member(hass):
    """Before the group owns a setpoint, it shows the active member's."""
    hass.states.async_set("climate.floor", "heat", {ATTR_TEMPERATURE: 21.0})
    assert _conductor(hass, HVACMode.HEAT).target_temperature == 21.0


async def test_target_temperature_prefers_authoritative_value(hass):
    """Once the group owns a setpoint, that wins over the member's."""
    hass.states.async_set("climate.floor", "heat", {ATTR_TEMPERATURE: 21.0})
    ent = _conductor(hass, HVACMode.HEAT)
    ent._attr_target_temperature = 23.0
    assert ent.target_temperature == 23.0


async def test_temperature_bounds_mirror_active_member(hass):
    """min/max/step come from the active member so the UI slider matches it."""
    hass.states.async_set(
        "climate.floor",
        "heat",
        {ATTR_MIN_TEMP: 5, ATTR_MAX_TEMP: 30, ATTR_TARGET_TEMP_STEP: 0.5},
    )
    ent = _conductor(hass, HVACMode.HEAT)
    assert ent.min_temp == 5
    assert ent.max_temp == 30
    assert ent.target_temperature_step == 0.5


# --- Member listener (_member_changed) ------------------------------------


def _member_event(entity_id, state, attrs=None, context=None):
    """A state_changed event for one member (new_state only)."""
    new_state = State(entity_id, state, attrs or {})
    return Event(
        "state_changed",
        {"entity_id": entity_id, "new_state": new_state, "old_state": None},
        context=context,
    )


async def test_listener_does_not_reroute_on_our_own_echoes(hass):
    """An event carrying our context id must not re-route or change the mode."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    ent = _conductor(hass, HVACMode.HEAT)
    ours = Context(id=f"{CONDUCTOR_CONTEXT_PREFIX}whatever")
    # 'floor off' would normally take the group off; our echo must not.
    await ent._member_changed(_member_event("climate.floor", "off", context=ours))
    assert ent.hvac_mode == HVACMode.HEAT
    assert calls == []


async def test_listener_mirrors_display_on_our_own_echoes(hass):
    """The member's settled setpoint rides in on our command's context; the
    group's display must still reflect it rather than being dropped with the echo."""
    hass.states.async_set("climate.ac", "cool", {ATTR_TEMPERATURE: 24.0})
    ent = _conductor(hass, HVACMode.COOL)  # AC active, no setpoint owned yet
    ours = Context(id=f"{CONDUCTOR_CONTEXT_PREFIX}x")
    await ent._member_changed(
        _member_event("climate.ac", "cool", {ATTR_TEMPERATURE: 24.0}, context=ours)
    )
    state = hass.states.get("climate.conductor")
    assert state is not None
    assert state.attributes["temperature"] == 24.0


async def test_listener_adopts_active_member_setpoint(hass):
    """Same-mode setpoint change on the active member updates the group's display."""
    ent = _conductor(hass, HVACMode.HEAT)
    await ent._member_changed(
        _member_event("climate.floor", "heat", {ATTR_TEMPERATURE: 22.0})
    )
    assert ent.target_temperature == 22.0


async def test_listener_reroutes_when_member_poked_to_another_mode(hass):
    """Poking a member to a different mode makes routing authoritative."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    ent = _conductor(hass, HVACMode.HEAT)  # floor active
    await ent._member_changed(_member_event("climate.ac", "cool"))
    assert ent.hvac_mode == HVACMode.COOL
    assert _modes_by_member(calls) == {
        "climate.ac": HVACMode.COOL,
        "climate.floor": HVACMode.OFF,
    }


async def test_listener_takes_group_off_when_active_member_off(hass):
    """The active member turned off deliberately takes the group off."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    ent = _conductor(hass, HVACMode.HEAT)
    await ent._member_changed(_member_event("climate.floor", "off"))
    assert ent.hvac_mode == HVACMode.OFF
    assert all(m == HVACMode.OFF for m in _modes_by_member(calls).values())


async def test_listener_keeps_mode_when_active_member_unavailable(hass):
    """A transient unavailable keeps the mode; the group self-heals later."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    ent = _conductor(hass, HVACMode.HEAT)
    await ent._member_changed(_member_event("climate.floor", "unavailable"))
    assert ent.hvac_mode == HVACMode.HEAT
    assert calls == []


async def test_listener_ignores_inactive_member_turning_off(hass):
    """An already-off, non-active member going off changes nothing."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
    ent = _conductor(hass, HVACMode.HEAT)  # floor active, ac inactive
    await ent._member_changed(_member_event("climate.ac", "off"))
    assert ent.hvac_mode == HVACMode.HEAT
    assert calls == []


# --- Availability ---------------------------------------------------------


async def test_available_when_any_member_is_available(hass):
    """The group is available while at least one member is available."""
    hass.states.async_set("climate.floor", "off")
    hass.states.async_set("climate.ac", "unavailable")
    assert _conductor(hass, HVACMode.OFF).available is True


async def test_unavailable_when_all_members_unavailable(hass):
    """The group is unavailable only when every member is unavailable."""
    hass.states.async_set("climate.floor", "unavailable")
    hass.states.async_set("climate.ac", "unavailable")
    assert _conductor(hass, HVACMode.OFF).available is False


async def test_unavailable_when_no_member_states(hass):
    """A member with no state at all does not count as available."""
    assert _conductor(hass, HVACMode.OFF).available is False


# --- Mirroring action / fan / swing / preset ------------------------------


async def test_hvac_action_mirrors_active_member(hass):
    """hvac_action reflects the active member's action."""
    hass.states.async_set("climate.floor", "heat", {ATTR_HVAC_ACTION: "idle"})
    assert _conductor(hass, HVACMode.HEAT).hvac_action == "idle"


async def test_hvac_action_none_when_off(hass):
    """With no active member there is no action to show."""
    assert _conductor(hass, HVACMode.OFF).hvac_action is None


async def test_fan_mode_and_modes_pass_through(hass):
    """fan_mode and fan_modes come from the active member."""
    hass.states.async_set(
        "climate.ac", "cool", {ATTR_FAN_MODE: "high", ATTR_FAN_MODES: ["low", "high"]}
    )
    ent = _conductor(hass, HVACMode.COOL)
    assert ent.fan_mode == "high"
    assert ent.fan_modes == ["low", "high"]


async def test_swing_and_preset_pass_through(hass):
    """swing_mode/preset_mode and their lists come from the active member."""
    hass.states.async_set(
        "climate.ac",
        "cool",
        {
            ATTR_SWING_MODE: "both",
            ATTR_SWING_MODES: ["off", "both"],
            ATTR_PRESET_MODE: "eco",
            ATTR_PRESET_MODES: ["eco", "boost"],
        },
    )
    ent = _conductor(hass, HVACMode.COOL)
    assert ent.swing_mode == "both"
    assert ent.swing_modes == ["off", "both"]
    assert ent.preset_mode == "eco"
    assert ent.preset_modes == ["eco", "boost"]


async def test_supported_features_include_active_member_capabilities(hass):
    """We keep our own TARGET_TEMPERATURE and add the member's fan/swing/preset."""
    caps = ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.PRESET_MODE
    hass.states.async_set("climate.ac", "cool", {ATTR_SUPPORTED_FEATURES: int(caps)})
    feats = _conductor(hass, HVACMode.COOL).supported_features
    assert feats & ClimateEntityFeature.TARGET_TEMPERATURE
    assert feats & ClimateEntityFeature.FAN_MODE
    assert feats & ClimateEntityFeature.PRESET_MODE


async def test_supported_features_off_is_target_temperature_only(hass):
    """With no active member we advertise only our own setpoint support."""
    ent = _conductor(hass, HVACMode.OFF)
    assert ent.supported_features == ClimateEntityFeature.TARGET_TEMPERATURE


async def test_set_fan_mode_forwards_to_active_member(hass):
    """Setting fan mode forwards to the active member, echo-tagged."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_FAN_MODE)
    await _conductor(hass, HVACMode.COOL).async_set_fan_mode("high")
    assert calls[0].data[ATTR_ENTITY_ID] == "climate.ac"
    assert calls[0].data[ATTR_FAN_MODE] == "high"
    assert calls[0].context.id.startswith(CONDUCTOR_CONTEXT_PREFIX)


async def test_set_swing_mode_forwards_to_active_member(hass):
    """Setting swing mode forwards to the active member."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_SWING_MODE)
    await _conductor(hass, HVACMode.COOL).async_set_swing_mode("both")
    assert calls[0].data[ATTR_ENTITY_ID] == "climate.ac"
    assert calls[0].data[ATTR_SWING_MODE] == "both"


async def test_set_preset_mode_forwards_to_active_member(hass):
    """Setting preset mode forwards to the active member."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_PRESET_MODE)
    await _conductor(hass, HVACMode.COOL).async_set_preset_mode("eco")
    assert calls[0].data[ATTR_ENTITY_ID] == "climate.ac"
    assert calls[0].data[ATTR_PRESET_MODE] == "eco"


# --- heat_cool range setpoints --------------------------------------------


async def test_supported_features_include_range_from_active_member(hass):
    """When the active member offers a low/high band, so does the group."""
    caps = ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    hass.states.async_set(
        "climate.ac", "heat_cool", {ATTR_SUPPORTED_FEATURES: int(caps)}
    )
    feats = _conductor(hass, HVACMode.HEAT_COOL).supported_features
    assert feats & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE


async def test_target_temperature_range_mirrors_active_member(hass):
    """target_temperature_low/high come from the active member."""
    hass.states.async_set(
        "climate.ac",
        "heat_cool",
        {ATTR_TARGET_TEMP_LOW: 19.0, ATTR_TARGET_TEMP_HIGH: 24.0},
    )
    ent = _conductor(hass, HVACMode.HEAT_COOL)
    assert ent.target_temperature_low == 19.0
    assert ent.target_temperature_high == 24.0


async def test_set_temperature_range_forwards_to_active_member(hass):
    """A low/high setpoint is forwarded to the active member, echo-tagged."""
    calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE)
    await _conductor(hass, HVACMode.HEAT_COOL).async_set_temperature(
        target_temp_low=19.0, target_temp_high=24.0
    )
    assert calls[0].data[ATTR_ENTITY_ID] == "climate.ac"
    assert calls[0].data[ATTR_TARGET_TEMP_LOW] == 19.0
    assert calls[0].data[ATTR_TARGET_TEMP_HIGH] == 24.0
    assert calls[0].context.id.startswith(CONDUCTOR_CONTEXT_PREFIX)
