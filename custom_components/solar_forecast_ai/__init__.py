"""Solar Forecast AI - Home Assistant integration.

Predicts solar energy yield and self-consumption using:
- Open-Meteo free weather API (no API key required)
- Physics-based PV model
- Adaptive AI correction that learns from actual production data
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import SolarForecastCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solar Forecast AI from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = SolarForecastCoordinator(hass, entry.entry_id, dict(entry.data | entry.options))
    await coordinator.async_setup()

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Kan zonpanel data niet ophalen: {err}") from err

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))
    entry.async_on_unload(coordinator.async_shutdown)

    _LOGGER.info(
        "Solar Forecast AI gestart: %.1f kWp, PR=%.2f, locatie=(%.4f, %.4f)",
        coordinator.predictor.peak_power_kwp,
        coordinator.predictor.performance_ratio,
        hass.config.latitude,
        hass.config.longitude,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    coordinator: SolarForecastCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.update_config(dict(entry.data | entry.options))
    await coordinator.async_request_refresh()
