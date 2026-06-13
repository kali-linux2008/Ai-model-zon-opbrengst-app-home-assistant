"""Sensor platform for Solar Forecast AI."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ATTRIBUTION,
    SENSOR_FORECAST_TODAY,
    SENSOR_FORECAST_TOMORROW,
    SENSOR_FORECAST_7DAYS,
    SENSOR_FORECAST_POWER_NOW,
    SENSOR_PEAK_POWER_TODAY,
    SENSOR_PEAK_TIME_TODAY,
    SENSOR_SELF_CONSUMPTION_TODAY,
    SENSOR_SELF_SUFFICIENCY_TODAY,
    SENSOR_CORRECTION_FACTOR,
    SENSOR_FORECAST_TOMORROW_PEAK,
)
from .coordinator import SolarForecastCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SolarSensorDescription(SensorEntityDescription):
    """Describes a Solar Forecast AI sensor."""
    data_key: str = ""
    extra_attrs_fn: Any = None  # callable(coordinator_data) -> dict


SENSOR_DESCRIPTIONS: tuple[SolarSensorDescription, ...] = (
    SolarSensorDescription(
        key=SENSOR_FORECAST_TODAY,
        data_key="today_kwh",
        name="Zonopbrengst Vandaag",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:solar-power",
        suggested_display_precision=2,
        extra_attrs_fn=lambda d: {
            "piek_vandaag_w": d.get("peak_power_today_w"),
            "piek_tijdstip": d.get("peak_time_today"),
            "zelfverbruik_pct": d.get("self_consumption_today_pct"),
            "zelfredzaamheid_pct": d.get("self_sufficiency_today_pct"),
            "vertrouwen_pct": d.get("confidence_pct"),
        },
    ),
    SolarSensorDescription(
        key=SENSOR_FORECAST_TOMORROW,
        data_key="tomorrow_kwh",
        name="Zonopbrengst Morgen",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:solar-power",
        suggested_display_precision=2,
        extra_attrs_fn=lambda d: {
            "piek_morgen_w": d.get("peak_power_tomorrow_w"),
            "vertrouwen_pct": d.get("confidence_pct"),
        },
    ),
    SolarSensorDescription(
        key=SENSOR_FORECAST_7DAYS,
        data_key="total_7days_kwh",
        name="Zonopbrengst 7 Dagen",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:solar-power-variant",
        suggested_display_precision=1,
        extra_attrs_fn=lambda d: {
            "dagelijks_totaal": d.get("daily_totals", {}),
        },
    ),
    SolarSensorDescription(
        key=SENSOR_FORECAST_POWER_NOW,
        data_key="power_now_w",
        name="Verwacht Zonnestroom Nu",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        suggested_display_precision=0,
    ),
    SolarSensorDescription(
        key=SENSOR_PEAK_POWER_TODAY,
        data_key="peak_power_today_w",
        name="Verwacht Piekvermogen Vandaag",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
        suggested_display_precision=0,
    ),
    SolarSensorDescription(
        key=SENSOR_PEAK_TIME_TODAY,
        data_key="peak_time_today",
        name="Verwacht Piekvermogen Tijdstip",
        native_unit_of_measurement=None,
        device_class=None,
        state_class=None,
        icon="mdi:clock-outline",
    ),
    SolarSensorDescription(
        key=SENSOR_SELF_CONSUMPTION_TODAY,
        data_key="self_consumption_today_pct",
        name="Verwacht Zelfverbruik Vandaag",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-lightning-bolt",
        suggested_display_precision=1,
    ),
    SolarSensorDescription(
        key=SENSOR_SELF_SUFFICIENCY_TODAY,
        data_key="self_sufficiency_today_pct",
        name="Verwachte Zelfredzaamheid Vandaag",
        native_unit_of_measurement=PERCENTAGE,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:home-battery",
        suggested_display_precision=1,
    ),
    SolarSensorDescription(
        key=SENSOR_CORRECTION_FACTOR,
        data_key="correction_factor",
        name="AI Correctiefactor",
        native_unit_of_measurement=None,
        device_class=None,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:brain",
        suggested_display_precision=3,
        extra_attrs_fn=lambda d: {
            "leerdagen": d.get("learning_days", 0),
            "vertrouwen_pct": d.get("confidence_pct", 40),
            "laatste_update": d.get("last_updated", ""),
        },
    ),
    SolarSensorDescription(
        key=SENSOR_FORECAST_TOMORROW_PEAK,
        data_key="peak_power_tomorrow_w",
        name="Verwacht Piekvermogen Morgen",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-panel",
        suggested_display_precision=0,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solar Forecast AI sensors."""
    coordinator: SolarForecastCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        SolarForecastSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    async_add_entities(entities)


class SolarForecastSensor(CoordinatorEntity[SolarForecastCoordinator], SensorEntity):
    """Sensor entity for Solar Forecast AI."""

    entity_description: SolarSensorDescription
    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarForecastCoordinator,
        entry: ConfigEntry,
        description: SolarSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._entry = entry

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Solar Forecast AI",
            manufacturer="Open-Meteo + AI Model",
            model=f"{coordinator.predictor.peak_power_kwp} kWp",
            sw_version="1.0.0",
            configuration_url="https://github.com/kali-linux2008/ai-model-zon-opbrengst-app-home-assistant",
        )

    @property
    def native_value(self) -> Any:
        """Return sensor value from coordinator data."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.entity_description.data_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes if defined."""
        attrs: dict[str, Any] = {}
        if self.coordinator.data and self.entity_description.extra_attrs_fn:
            try:
                attrs = self.entity_description.extra_attrs_fn(self.coordinator.data)
            except Exception:
                pass

        # Add hourly forecast to main forecast sensors
        if self.entity_description.key == SENSOR_FORECAST_TODAY and self.coordinator.data:
            hourly = self.coordinator.data.get("hourly_forecast", [])
            today = self.coordinator.hass.states.get("sensor.date")
            today_str = self.coordinator.data.get("last_updated", "")[:10]
            attrs["uurlijkse_voorspelling"] = [
                {
                    "tijd": e["time"][11:16],
                    "vermogen_w": e["power_w"],
                    "straling_wm2": e["radiation_wm2"],
                    "bewolking_pct": e.get("cloudcover_pct", 0),
                }
                for e in hourly if e["time"].startswith(today_str)
            ]

        return attrs
