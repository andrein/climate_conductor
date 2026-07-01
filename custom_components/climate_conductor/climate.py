"""Climate platform for Climate Conductor."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import (
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util.ulid import ulid_now

from .const import (
    CONDUCTOR_CONTEXT_PREFIX,
    CONF_HIDE_MEMBERS,
    CONF_ROUTES,
    CONF_TEMPERATURE_SENSOR,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Climate Conductor entity."""
    async_add_entities([ClimateConductor(entry)])


class ClimateConductor(ClimateEntity):
    """Room thermostat that routes each HVAC mode to one member. See ARCHITECTURE.md."""

    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    # We own the single setpoint; the rest is passed through from the member.
    _PASS_THROUGH_FEATURES = (
        ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.PRESET_MODE
    )

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialise from the config entry."""
        self._entry = entry
        options = {**entry.data, **entry.options}
        self._routes: dict[str, str] = dict(options.get(CONF_ROUTES, {}))
        self._temperature_sensor: str | None = options.get(CONF_TEMPERATURE_SENSOR)
        self._hide_members: bool = options.get(CONF_HIDE_MEMBERS, False)

        self._attr_unique_id = entry.entry_id
        self._attr_name = entry.title
        self._attr_hvac_mode = HVACMode.OFF  # authoritative; not derived from members
        self._attr_target_temperature: float | None = None

    @property
    def members(self) -> set[str]:
        """Member entity ids."""
        return set(self._routes.values())

    @property
    def active_member(self) -> str | None:
        """Member serving the current mode, or None when off."""
        mode = self._attr_hvac_mode
        if mode is None or mode == HVACMode.OFF:
            return None
        return self._routes.get(mode)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Supported HVAC modes."""
        # from config, not live members, so the picker never flickers on drop-out
        return [HVACMode.OFF] + [HVACMode(m) for m in self._routes if m != HVACMode.OFF]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """State attributes."""
        # entity_id list drives the native more-info member list
        return {ATTR_ENTITY_ID: sorted(self.members)}

    @property
    def available(self) -> bool:
        """Available while at least one member is available."""
        return any(
            (state := self.hass.states.get(member)) is not None
            and state.state != STATE_UNAVAILABLE
            for member in self.members
        )

    def _active_member_attr(self, attr: str) -> Any | None:
        """Read an attribute from the active member's live state, if available."""
        if (member := self.active_member) is None:
            return None
        if (state := self.hass.states.get(member)) is None:
            return None
        return state.attributes.get(attr)

    @property
    def current_temperature(self) -> float | None:
        """Current temperature: override sensor if configured, else active member."""
        if self._temperature_sensor:
            state = self.hass.states.get(self._temperature_sensor)
            if state is not None:
                try:
                    return float(state.state)
                except (ValueError, TypeError):
                    return None
        return self._active_member_attr(ATTR_CURRENT_TEMPERATURE)

    @property
    def target_temperature(self) -> float | None:
        """Our authoritative setpoint, falling back to the active member's."""
        if self._attr_target_temperature is not None:
            return self._attr_target_temperature
        return self._active_member_attr(ATTR_TEMPERATURE)

    @property
    def target_temperature_low(self) -> float | None:
        """Low edge of the active member's range (heat_cool)."""
        return self._active_member_attr(ATTR_TARGET_TEMP_LOW)

    @property
    def target_temperature_high(self) -> float | None:
        """High edge of the active member's range (heat_cool)."""
        return self._active_member_attr(ATTR_TARGET_TEMP_HIGH)

    @property
    def min_temp(self) -> float:
        """Minimum settable temperature, mirrored from the active member."""
        value = self._active_member_attr(ATTR_MIN_TEMP)
        return value if value is not None else super().min_temp

    @property
    def max_temp(self) -> float:
        """Maximum settable temperature, mirrored from the active member."""
        value = self._active_member_attr(ATTR_MAX_TEMP)
        return value if value is not None else super().max_temp

    @property
    def target_temperature_step(self) -> float | None:
        """Setpoint step, mirrored from the active member."""
        value = self._active_member_attr(ATTR_TARGET_TEMP_STEP)
        return value if value is not None else super().target_temperature_step

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Our own setpoint support plus the active member's fan/swing/preset."""
        features = ClimateEntityFeature.TARGET_TEMPERATURE
        member_features = self._active_member_attr(ATTR_SUPPORTED_FEATURES)
        if member_features:
            features |= (
                ClimateEntityFeature(member_features) & self._PASS_THROUGH_FEATURES
            )
        return features

    @property
    def hvac_action(self) -> Any | None:
        """Action reported by the active member."""
        return self._active_member_attr(ATTR_HVAC_ACTION)

    @property
    def fan_mode(self) -> str | None:
        """Fan mode of the active member."""
        return self._active_member_attr(ATTR_FAN_MODE)

    @property
    def fan_modes(self) -> list[str] | None:
        """Fan modes offered by the active member."""
        return self._active_member_attr(ATTR_FAN_MODES)

    @property
    def swing_mode(self) -> str | None:
        """Swing mode of the active member."""
        return self._active_member_attr(ATTR_SWING_MODE)

    @property
    def swing_modes(self) -> list[str] | None:
        """Swing modes offered by the active member."""
        return self._active_member_attr(ATTR_SWING_MODES)

    @property
    def preset_mode(self) -> str | None:
        """Preset mode of the active member."""
        return self._active_member_attr(ATTR_PRESET_MODE)

    @property
    def preset_modes(self) -> list[str] | None:
        """Presets offered by the active member."""
        return self._active_member_attr(ATTR_PRESET_MODES)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set a new HVAC mode."""
        self._attr_hvac_mode = hvac_mode
        await self._apply_routing()
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a single setpoint or a heat_cool range, forwarding to the member."""
        member = self.active_member
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._attr_target_temperature = temperature  # authoritative, even off
            if member is not None:
                await self._forward_temperature(member)
        low = kwargs.get(ATTR_TARGET_TEMP_LOW)
        high = kwargs.get(ATTR_TARGET_TEMP_HIGH)
        if low is not None and high is not None and member is not None:
            await self._forward_to_active(
                SERVICE_SET_TEMPERATURE,
                {ATTR_TARGET_TEMP_LOW: low, ATTR_TARGET_TEMP_HIGH: high},
            )
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Forward a fan mode change to the active member."""
        await self._forward_to_active(SERVICE_SET_FAN_MODE, {ATTR_FAN_MODE: fan_mode})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Forward a swing mode change to the active member."""
        await self._forward_to_active(
            SERVICE_SET_SWING_MODE, {ATTR_SWING_MODE: swing_mode}
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Forward a preset change to the active member."""
        await self._forward_to_active(
            SERVICE_SET_PRESET_MODE, {ATTR_PRESET_MODE: preset_mode}
        )

    async def _forward_to_active(self, service: str, data: dict[str, Any]) -> None:
        """Call a climate service on the active member, echo-tagged; no-op if off."""
        if (member := self.active_member) is None:
            return
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            service,
            {ATTR_ENTITY_ID: member, **data},
            blocking=True,
            context=self._command_context(),
        )

    async def _apply_routing(self) -> None:
        """Drive the member for the current mode; turn the rest off."""
        active = self.active_member
        for member in self.members:
            target = self._attr_hvac_mode if member == active else HVACMode.OFF
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_ENTITY_ID: member, ATTR_HVAC_MODE: target},
                blocking=True,
                context=self._command_context(),
            )
        if active is not None:
            # adopt the new member's own setpoint rather than bleeding the old
            # mode's onto it; an explicit change still forwards via set_temperature
            self._attr_target_temperature = self._active_member_attr(ATTR_TEMPERATURE)

    async def _forward_temperature(self, member: str) -> None:
        """Send the stored setpoint to a member, echo-tagged; no-op if unset."""
        if self._attr_target_temperature is None:
            return
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: member, ATTR_TEMPERATURE: self._attr_target_temperature},
            blocking=True,
            context=self._command_context(),
        )

    def _command_context(self) -> Context:
        """A context tagged so the member listener drops echoes of our writes."""
        # Overwrite the ULID's timestamp head with our prefix; the random tail
        # keeps it unique and the total length stays within HA's 26-char id.
        suffix = ulid_now()[len(CONDUCTOR_CONTEXT_PREFIX) :]
        return Context(id=f"{CONDUCTOR_CONTEXT_PREFIX}{suffix}")

    async def async_added_to_hass(self) -> None:
        """Subscribe to member state changes."""
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, list(self.members), self._member_changed
            )
        )

    async def _member_changed(self, event: Event[EventStateChangedData]) -> None:
        """Normalize an out-of-band change on a watched member."""
        context = event.context
        if context is not None and context.id.startswith(CONDUCTOR_CONTEXT_PREFIX):
            # the member's settled state rides in on our command's context;
            # mirror it for display, but don't re-route (that would loop)
            self.async_write_ha_state()
            return

        new_state = event.data.get("new_state")
        if new_state is None:
            self.async_write_ha_state()
            return

        member_mode = new_state.state
        is_active = event.data["entity_id"] == self.active_member

        if member_mode in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self.async_write_ha_state()
            return

        if member_mode == HVACMode.OFF:
            if is_active:
                self._attr_hvac_mode = HVACMode.OFF
                await self._apply_routing()
            self.async_write_ha_state()
            return

        if is_active and member_mode == self._attr_hvac_mode:
            temperature = new_state.attributes.get(ATTR_TEMPERATURE)
            if temperature is not None:
                self._attr_target_temperature = temperature
            self.async_write_ha_state()
            return

        if member_mode in self._routes:
            self._attr_hvac_mode = HVACMode(member_mode)
        await self._apply_routing()
        self.async_write_ha_state()
