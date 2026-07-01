"""Climate platform for Climate Conductor."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import ClimateEntity, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_HIDE_MEMBERS, CONF_ROUTES, CONF_TEMPERATURE_SENSOR

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

    @property
    def current_temperature(self) -> float | None:
        """Current temperature."""
        return None  # TODO: override sensor, else active member, else fallback

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set a new HVAC mode."""
        self._attr_hvac_mode = hvac_mode
        await self._apply_routing()
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature."""
        raise NotImplementedError  # TODO: forward to active member, echo-tagged

    async def _apply_routing(self) -> None:
        """Drive the member for the current mode; turn the rest off."""
        # commands must carry a CONDUCTOR_CONTEXT_PREFIX context (echo suppression)
        raise NotImplementedError

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
