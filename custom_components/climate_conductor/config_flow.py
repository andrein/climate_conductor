"""Config flow for Climate Conductor."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN, SensorDeviceClass
from homeassistant.const import CONF_NAME
from homeassistant.helpers import selector
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaConfigFlowHandler,
    SchemaFlowFormStep,
)
import voluptuous as vol

from .const import CONF_HIDE_MEMBERS, CONF_MEMBERS, CONF_TEMPERATURE_SENSOR, DOMAIN

BASE_SCHEMA = {
    vol.Required(CONF_MEMBERS): selector.EntitySelector(
        selector.EntitySelectorConfig(domain=CLIMATE_DOMAIN, multiple=True),
    ),
    vol.Optional(CONF_TEMPERATURE_SENSOR): selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain=SENSOR_DOMAIN, device_class=SensorDeviceClass.TEMPERATURE
        ),
    ),
    vol.Required(CONF_HIDE_MEMBERS, default=True): selector.BooleanSelector(),
}

# TODO: per-mode routing step — seed CONF_ROUTES from member capabilities,
# prompt only for modes more than one member can serve.
CONFIG_FLOW = {
    "user": SchemaFlowFormStep(
        vol.Schema({vol.Required(CONF_NAME): selector.TextSelector()}).extend(
            BASE_SCHEMA
        )
    ),
}

OPTIONS_FLOW = {
    "init": SchemaFlowFormStep(vol.Schema(BASE_SCHEMA)),
}


class ClimateConductorConfigFlow(SchemaConfigFlowHandler, domain=DOMAIN):
    """Config and options flow for Climate Conductor."""

    config_flow = CONFIG_FLOW
    options_flow = OPTIONS_FLOW

    def async_config_entry_title(self, options: Mapping[str, Any]) -> str:
        """Return the config entry title."""
        return cast(str, options.get(CONF_NAME, "Climate Conductor"))
