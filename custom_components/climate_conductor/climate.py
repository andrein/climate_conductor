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
    ATTR_HVAC_MODE,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import Context, Event, HomeAssistant, callback
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
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

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
        self._attr_target_temperature: float | None = None  # authoritative setpoint

    @property
    def members(self) -> set[str]:
        """Member entity ids."""
        return set(self._routes.values())

    @property
    def active_member(self) -> str | None:
        """Member serving the current mode, or None when off."""
        if self._attr_hvac_mode == HVACMode.OFF:
            return None
        return self._routes.get(self._attr_hvac_mode)

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
        """Whether the entity is available."""
        return True  # TODO: any(member available)

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

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set a new HVAC mode."""
        self._attr_hvac_mode = hvac_mode
        await self._apply_routing()
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature, forwarding it to the active member."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        self._attr_target_temperature = temperature  # authoritative, even when off
        if (member := self.active_member) is not None:
            await self._forward_temperature(member)
        self.async_write_ha_state()

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
        # carry the authoritative setpoint onto whichever member now serves the mode
        if active is not None:
            await self._forward_temperature(active)

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

    @callback
    def _member_changed(self, event: Event) -> None:
        """Handle an out-of-band member change."""
        # TODO: drop own echoes; adopt same-mode setpoint; re-route on mode
        # change; group off when the active member is turned off.
        self.async_write_ha_state()
