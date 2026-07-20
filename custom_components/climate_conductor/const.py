"""Constants for the Climate Conductor integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "climate_conductor"
PLATFORMS: list[Platform] = [Platform.CLIMATE]

CONF_AREA = "area"
CONF_MEMBERS = "members"
CONF_ROUTES = "routes"  # {hvac_mode: member_entity_id}
CONF_TEMPERATURE_SENSOR = "temperature_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_HIDE_MEMBERS = "hide_members"

DEFAULT_NAME = "Thermostat"

# Context.id prefix on every command we send to members, so the member listener
# can drop echoes of our own writes and not re-trigger routing.
CONDUCTOR_CONTEXT_PREFIX = "climate_conductor"
