"""Data coordinator for Solar Forecast AI.

Manages data fetching, caching, learning updates, and sensor state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_PEAK_POWER,
    CONF_PERFORMANCE_RATIO,
    CONF_PRODUCTION_SENSOR,
    CONF_CONSUMPTION_SENSOR,
    CONF_FORECAST_DAYS,
    DEFAULT_FORECAST_DAYS,
    UPDATE_INTERVAL_HOURS,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .predictor import SolarPredictor

_LOGGER = logging.getLogger(__name__)


class SolarForecastCoordinator(DataUpdateCoordinator):
    """Coordinator that fetches weather data and runs the AI predictor.

    Responsibilities:
    - Periodic weather fetch via Open-Meteo
    - Feeding actual production data into the AI model for learning
    - Persisting learned model state across HA restarts
    - Monitoring consumption sensor for self-consumption profile building
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, config: dict[str, Any]) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
        )
        self.entry_id = entry_id
        self.config = config

        self._store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry_id}")
        self._production_sensor = config.get(CONF_PRODUCTION_SENSOR)
        self._consumption_sensor = config.get(CONF_CONSUMPTION_SENSOR)
        self._forecast_days = config.get(CONF_FORECAST_DAYS, DEFAULT_FORECAST_DAYS)
        self._unsub_callbacks: list[Any] = []

        # Build predictor using HA location
        ha_tz = str(hass.config.time_zone) or "UTC"
        self.predictor = SolarPredictor(
            peak_power_kwp=float(config[CONF_PEAK_POWER]),
            performance_ratio=float(config[CONF_PERFORMANCE_RATIO]),
            latitude=hass.config.latitude,
            longitude=hass.config.longitude,
            timezone_str=ha_tz,
        )

        # In-memory cache of last successful weather data
        self._weather_data: dict[str, Any] | None = None
        self._last_production_check: str | None = None

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Load persisted state and subscribe to sensor changes."""
        saved = await self._store.async_load()
        if saved:
            self.predictor.restore_state_dict(saved)

        if self._production_sensor:
            self._unsub_callbacks.append(
                async_track_state_change_event(
                    self.hass,
                    [self._production_sensor],
                    self._handle_production_change,
                )
            )
            _LOGGER.debug("Tracking production sensor: %s", self._production_sensor)

        if self._consumption_sensor:
            self._unsub_callbacks.append(
                async_track_state_change_event(
                    self.hass,
                    [self._consumption_sensor],
                    self._handle_consumption_change,
                )
            )
            _LOGGER.debug("Tracking consumption sensor: %s", self._consumption_sensor)

    async def async_shutdown(self) -> None:
        """Unsubscribe listeners."""
        for unsub in self._unsub_callbacks:
            unsub()
        self._unsub_callbacks.clear()

    # ------------------------------------------------------------------
    # Data update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch weather and compute forecasts. Called by HA every hour."""
        session = async_get_clientsession(self.hass)

        try:
            self._weather_data = await self.predictor.fetch_weather_data(session)
        except aiohttp.ClientError as err:
            if self._weather_data is None:
                raise UpdateFailed(f"Kan weerdata niet ophalen: {err}") from err
            _LOGGER.warning("Weerdata fetch mislukt, gebruik gecachede data: %s", err)

        hourly = self.predictor.calculate_hourly_production(
            self._weather_data, days=7
        )
        daily_totals = self.predictor.calculate_daily_totals(hourly)

        now_local = dt_util.now()
        today_str = now_local.strftime("%Y-%m-%d")
        tomorrow_str = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")

        today_kwh = daily_totals.get(today_str, 0.0)
        tomorrow_kwh = daily_totals.get(tomorrow_str, 0.0)
        total_7days = sum(list(daily_totals.values())[:7])

        # Current hour's forecast power
        current_hour_str = now_local.strftime("%Y-%m-%dT%H:")
        current_entry = next(
            (e for e in hourly if e["time"].startswith(current_hour_str)), None
        )
        power_now_w = current_entry["power_w"] if current_entry else 0.0

        # Peak for today
        peak_power_today, peak_time_today = self.predictor.get_peak_for_day(hourly, today_str)
        peak_power_tomorrow, _ = self.predictor.get_peak_for_day(hourly, tomorrow_str)

        # Self-consumption for today
        sc_today, ss_today = self.predictor.predict_self_consumption(hourly, date=today_str)

        # Try to learn from yesterday's actual production
        await self._maybe_learn_from_yesterday(daily_totals)

        return {
            "hourly_forecast": hourly[:self._forecast_days * 24],
            "daily_totals": daily_totals,
            "today_kwh": round(today_kwh, 3),
            "tomorrow_kwh": round(tomorrow_kwh, 3),
            "total_7days_kwh": round(total_7days, 3),
            "power_now_w": round(power_now_w, 1),
            "peak_power_today_w": round(peak_power_today, 1),
            "peak_time_today": peak_time_today,
            "peak_power_tomorrow_w": round(peak_power_tomorrow, 1),
            "self_consumption_today_pct": sc_today,
            "self_sufficiency_today_pct": ss_today,
            "correction_factor": self.predictor.correction_factor,
            "confidence_pct": self.predictor.confidence_pct,
            "learning_days": self.predictor.learning_days,
            "last_updated": now_local.isoformat(),
        }

    # ------------------------------------------------------------------
    # Learning from actual production
    # ------------------------------------------------------------------

    async def _maybe_learn_from_yesterday(
        self, daily_totals: dict[str, float]
    ) -> None:
        """Check if yesterday's actual production is available and learn from it."""
        if not self._production_sensor:
            return

        now_local = dt_util.now()
        yesterday_str = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")

        # Only learn once per day
        if self._last_production_check == yesterday_str:
            return

        predicted_yesterday = daily_totals.get(yesterday_str)
        if predicted_yesterday is None:
            return

        # Try to read yesterday's actual production from the sensor statistics
        # We use the current state as an approximation if it's a daily total sensor
        actual_kwh = await self._get_daily_production_kwh(yesterday_str)
        if actual_kwh is not None and actual_kwh > 0:
            self.predictor.learn_from_actual(yesterday_str, predicted_yesterday, actual_kwh)
            self._last_production_check = yesterday_str
            await self._store.async_save(self.predictor.get_state_dict())
            _LOGGER.info(
                "AI model updated: gisteren voorspeld=%.2f kWh, werkelijk=%.2f kWh, "
                "correctiefactor=%.3f",
                predicted_yesterday, actual_kwh, self.predictor.correction_factor
            )

    async def _get_daily_production_kwh(self, date_str: str) -> float | None:
        """Try to retrieve actual daily production from the production sensor."""
        if not self._production_sensor:
            return None

        state = self.hass.states.get(self._production_sensor)
        if state is None or state.state in ("unavailable", "unknown", ""):
            return None

        try:
            value = float(state.state)
        except (ValueError, TypeError):
            return None

        # Only use if the sensor is a today/daily type sensor and today's date matches yesterday
        # (i.e., the sensor just reset at midnight and we're reading the previous day's total)
        # This is a best-effort approach; users can also provide historical stats sensors
        today_str = dt_util.now().strftime("%Y-%m-%d")
        if date_str == today_str:
            return value

        # For yesterday: check sensor attributes for last reset date
        last_reset = state.attributes.get("last_reset", "")
        if last_reset and last_reset.startswith(date_str):
            return value

        return None

    # ------------------------------------------------------------------
    # Sensor state change handlers
    # ------------------------------------------------------------------

    @callback
    def _handle_production_change(self, event: Any) -> None:
        """Process production sensor state changes for same-day learning."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return
        try:
            kwh = float(new_state.state)
        except (ValueError, TypeError):
            return

        # Store for potential end-of-day learning
        _LOGGER.debug("Production sensor update: %.3f kWh", kwh)

    @callback
    def _handle_consumption_change(self, event: Any) -> None:
        """Process consumption sensor state changes to build usage profile."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return
        try:
            value = float(new_state.state)
        except (ValueError, TypeError):
            return

        # Determine unit - sensor may report in W or kW
        unit = new_state.attributes.get("unit_of_measurement", "W")
        if unit in ("W", "w"):
            consumption_kw = value / 1000.0
        else:
            consumption_kw = value  # assume kW

        hour = dt_util.now().hour
        self.predictor.update_consumption_profile(hour, consumption_kw)

    # ------------------------------------------------------------------
    # Config updates
    # ------------------------------------------------------------------

    def update_config(self, new_config: dict[str, Any]) -> None:
        """Apply updated configuration options."""
        if CONF_PEAK_POWER in new_config:
            self.predictor.peak_power_kwp = float(new_config[CONF_PEAK_POWER])
        if CONF_PERFORMANCE_RATIO in new_config:
            self.predictor.performance_ratio = float(new_config[CONF_PERFORMANCE_RATIO])
        if CONF_FORECAST_DAYS in new_config:
            self._forecast_days = int(new_config[CONF_FORECAST_DAYS])
        if CONF_PRODUCTION_SENSOR in new_config:
            self._production_sensor = new_config[CONF_PRODUCTION_SENSOR]
        if CONF_CONSUMPTION_SENSOR in new_config:
            self._consumption_sensor = new_config[CONF_CONSUMPTION_SENSOR]
        self.config.update(new_config)
