"""Config flow for Solar Forecast AI."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_PEAK_POWER,
    CONF_PERFORMANCE_RATIO,
    CONF_PRODUCTION_SENSOR,
    CONF_CONSUMPTION_SENSOR,
    CONF_FORECAST_DAYS,
    DEFAULT_PEAK_POWER,
    DEFAULT_PERFORMANCE_RATIO,
    DEFAULT_FORECAST_DAYS,
)

_LOGGER = logging.getLogger(__name__)


def _build_schema(
    peak_power: float = DEFAULT_PEAK_POWER,
    performance_ratio: float = DEFAULT_PERFORMANCE_RATIO,
    production_sensor: str = "",
    consumption_sensor: str = "",
    forecast_days: int = DEFAULT_FORECAST_DAYS,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PEAK_POWER, default=peak_power): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.1,
                    max=100.0,
                    step=0.1,
                    unit_of_measurement="kWp",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_PERFORMANCE_RATIO, default=performance_ratio): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.50,
                    max=1.00,
                    step=0.01,
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Required(CONF_FORECAST_DAYS, default=forecast_days): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=7,
                    step=1,
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Optional(CONF_PRODUCTION_SENSOR, default=production_sensor): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["sensor"],
                    device_class=["energy"],
                    multiple=False,
                )
            ),
            vol.Optional(CONF_CONSUMPTION_SENSOR, default=consumption_sensor): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["sensor"],
                    device_class=["energy", "power"],
                    multiple=False,
                )
            ),
        }
    )


class SolarForecastConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Solar Forecast AI."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not self.hass.config.latitude or not self.hass.config.longitude:
                errors["base"] = "no_location"
            elif float(user_input[CONF_PEAK_POWER]) <= 0:
                errors[CONF_PEAK_POWER] = "invalid_peak_power"
            else:
                # Convert number selector values to correct types
                data = {
                    CONF_PEAK_POWER: float(user_input[CONF_PEAK_POWER]),
                    CONF_PERFORMANCE_RATIO: float(user_input[CONF_PERFORMANCE_RATIO]),
                    CONF_FORECAST_DAYS: int(user_input[CONF_FORECAST_DAYS]),
                }
                if user_input.get(CONF_PRODUCTION_SENSOR):
                    data[CONF_PRODUCTION_SENSOR] = user_input[CONF_PRODUCTION_SENSOR]
                if user_input.get(CONF_CONSUMPTION_SENSOR):
                    data[CONF_CONSUMPTION_SENSOR] = user_input[CONF_CONSUMPTION_SENSOR]

                return self.async_create_entry(
                    title=f"Zonpanelen ({user_input[CONF_PEAK_POWER]} kWp)",
                    data=data,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(),
            errors=errors,
            description_placeholders={
                "latitude": str(round(self.hass.config.latitude, 4)),
                "longitude": str(round(self.hass.config.longitude, 4)),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return SolarForecastOptionsFlow(config_entry)


class SolarForecastOptionsFlow(config_entries.OptionsFlow):
    """Options flow to update settings after initial setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle options."""
        current = dict(self.config_entry.data | self.config_entry.options)

        if user_input is not None:
            options = {
                CONF_PEAK_POWER: float(user_input[CONF_PEAK_POWER]),
                CONF_PERFORMANCE_RATIO: float(user_input[CONF_PERFORMANCE_RATIO]),
                CONF_FORECAST_DAYS: int(user_input[CONF_FORECAST_DAYS]),
            }
            if user_input.get(CONF_PRODUCTION_SENSOR):
                options[CONF_PRODUCTION_SENSOR] = user_input[CONF_PRODUCTION_SENSOR]
            if user_input.get(CONF_CONSUMPTION_SENSOR):
                options[CONF_CONSUMPTION_SENSOR] = user_input[CONF_CONSUMPTION_SENSOR]

            return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(
                peak_power=current.get(CONF_PEAK_POWER, DEFAULT_PEAK_POWER),
                performance_ratio=current.get(CONF_PERFORMANCE_RATIO, DEFAULT_PERFORMANCE_RATIO),
                production_sensor=current.get(CONF_PRODUCTION_SENSOR, ""),
                consumption_sensor=current.get(CONF_CONSUMPTION_SENSOR, ""),
                forecast_days=current.get(CONF_FORECAST_DAYS, DEFAULT_FORECAST_DAYS),
            ),
        )
