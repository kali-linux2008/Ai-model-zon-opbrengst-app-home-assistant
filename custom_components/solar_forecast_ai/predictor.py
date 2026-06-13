"""AI-powered solar production and self-consumption predictor.

Uses Open-Meteo (free, no API key) for weather forecasts combined
with a physics-based PV model that learns from actual production data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from .const import (
    OPEN_METEO_URL,
    REQUEST_TIMEOUT,
    MAX_LEARNING_DAYS,
    MIN_LEARNING_DAYS,
    LEARNING_EMA_ALPHA,
    CONSUMPTION_EMA_ALPHA,
)

_LOGGER = logging.getLogger(__name__)

# Default European household consumption profile (kW per hour of day)
DEFAULT_CONSUMPTION_PROFILE = {
    0: 0.15, 1: 0.12, 2: 0.10, 3: 0.10, 4: 0.10, 5: 0.13,
    6: 0.22, 7: 0.38, 8: 0.42, 9: 0.36, 10: 0.30, 11: 0.32,
    12: 0.48, 13: 0.42, 14: 0.31, 15: 0.29, 16: 0.33, 17: 0.48,
    18: 0.62, 19: 0.68, 20: 0.72, 21: 0.62, 22: 0.46, 23: 0.26,
}


class SolarPredictor:
    """Physics-based solar predictor with adaptive ML correction.

    Algorithm:
    1. Fetch surface shortwave radiation from Open-Meteo (W/m², includes clouds)
    2. Apply temperature derating coefficient (-0.4%/°C from 25°C)
    3. Scale by peak power and performance ratio
    4. Apply learned correction factor from historical comparison
    5. Predict self-consumption from consumption profile

    The correction factor is updated via exponential moving average
    each time actual production data is available, making the model
    progressively more accurate for each specific installation.
    """

    def __init__(
        self,
        peak_power_kwp: float,
        performance_ratio: float,
        latitude: float,
        longitude: float,
        timezone_str: str,
    ) -> None:
        self.peak_power_kwp = peak_power_kwp
        self.performance_ratio = performance_ratio
        self.latitude = latitude
        self.longitude = longitude
        self.timezone_str = timezone_str

        # Learned parameters (persisted via coordinator)
        self.correction_factor: float = 1.0
        self.learning_data: list[dict[str, Any]] = []
        self.consumption_profile: dict[int, float] = dict(DEFAULT_CONSUMPTION_PROFILE)
        self.learning_days: int = 0

    # ------------------------------------------------------------------
    # Weather data
    # ------------------------------------------------------------------

    async def fetch_weather_data(self, session: Any) -> dict[str, Any]:
        """Fetch 7-day hourly weather forecast from Open-Meteo API."""
        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "hourly": "shortwave_radiation,temperature_2m,cloudcover,precipitation",
            "timezone": self.timezone_str,
            "forecast_days": 7,
        }
        try:
            async with session.get(
                OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT
            ) as response:
                response.raise_for_status()
                data = await response.json()
                _LOGGER.debug("Open-Meteo response received: %s hourly records", len(data.get("hourly", {}).get("time", [])))
                return data
        except Exception as err:
            _LOGGER.error("Failed to fetch weather data from Open-Meteo: %s", err)
            raise

    # ------------------------------------------------------------------
    # Production prediction
    # ------------------------------------------------------------------

    def calculate_hourly_production(
        self,
        weather_data: dict[str, Any],
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return hourly production forecast for the requested number of days."""
        hourly = weather_data.get("hourly", {})
        times = hourly.get("time", [])
        radiation = hourly.get("shortwave_radiation", [])
        temperatures = hourly.get("temperature_2m", [])
        cloudcover = hourly.get("cloudcover", [])
        precipitation = hourly.get("precipitation", [])

        results: list[dict[str, Any]] = []
        max_entries = days * 24

        for i in range(min(len(times), max_entries)):
            rad = radiation[i] if i < len(radiation) else 0
            temp = temperatures[i] if i < len(temperatures) else 20
            clouds = cloudcover[i] if i < len(cloudcover) else 50
            precip = precipitation[i] if i < len(precipitation) else 0

            if rad is None:
                rad = 0
            if temp is None:
                temp = 20

            # Temperature derating: silicon panels lose ~0.4%/°C above 25°C
            # Cell temperature is higher than ambient due to heating
            cell_temp = temp + (rad / 800) * 25  # NOCT model approximation
            temp_factor = 1.0 + (-0.004 * (cell_temp - 25))
            temp_factor = max(0.5, min(1.2, temp_factor))

            # Core formula: P = (G/G_ref) × P_peak × PR × temp_factor × correction
            power_w = (
                (rad / 1000.0)
                * self.peak_power_kwp
                * 1000.0
                * self.performance_ratio
                * temp_factor
                * self.correction_factor
            )
            power_w = max(0.0, power_w)

            results.append({
                "time": times[i],
                "power_w": round(power_w, 1),
                "energy_wh": round(power_w, 1),  # 1h intervals → Wh = W
                "radiation_wm2": round(rad, 1) if rad else 0,
                "temperature_c": round(temp, 1) if temp else 0,
                "cloudcover_pct": round(clouds, 0) if clouds else 0,
                "precipitation_mm": round(precip, 2) if precip else 0,
                "temp_factor": round(temp_factor, 3),
            })

        return results

    def calculate_daily_totals(
        self, hourly: list[dict[str, Any]]
    ) -> dict[str, float]:
        """Sum hourly Wh values per date, return kWh."""
        daily: dict[str, float] = {}
        for entry in hourly:
            date = entry["time"][:10]
            daily[date] = daily.get(date, 0.0) + entry["energy_wh"] / 1000.0
        return {d: round(v, 3) for d, v in daily.items()}

    def get_peak_for_day(
        self, hourly: list[dict[str, Any]], date: str
    ) -> tuple[float, str]:
        """Return (peak_power_w, peak_time_str) for a specific date."""
        day_entries = [e for e in hourly if e["time"].startswith(date)]
        if not day_entries:
            return 0.0, "--:--"
        peak = max(day_entries, key=lambda x: x["power_w"])
        peak_time = peak["time"][11:16]  # HH:MM
        return peak["power_w"], peak_time

    # ------------------------------------------------------------------
    # Adaptive learning
    # ------------------------------------------------------------------

    def learn_from_actual(
        self, date: str, predicted_kwh: float, actual_kwh: float
    ) -> None:
        """Update correction factor using actual vs predicted comparison.

        Uses exponential moving average so recent days have more weight.
        Only updates when production was significant (>0.5 kWh) to avoid
        noise from very cloudy or short days.
        """
        if predicted_kwh < 0.5 or actual_kwh < 0:
            return

        ratio = actual_kwh / predicted_kwh
        ratio = max(0.2, min(5.0, ratio))

        # Check if we already have data for this date
        existing = next((d for d in self.learning_data if d["date"] == date), None)
        if existing:
            existing["actual"] = actual_kwh
            existing["ratio"] = ratio
        else:
            self.learning_data.append({
                "date": date,
                "predicted": round(predicted_kwh, 3),
                "actual": round(actual_kwh, 3),
                "ratio": round(ratio, 3),
            })

        # Keep only recent days
        self.learning_data = sorted(self.learning_data, key=lambda x: x["date"])
        if len(self.learning_data) > MAX_LEARNING_DAYS:
            self.learning_data = self.learning_data[-MAX_LEARNING_DAYS:]

        self.learning_days = len(self.learning_data)

        # Recalculate correction factor with exponential weighting
        if self.learning_days >= MIN_LEARNING_DAYS:
            n = len(self.learning_data)
            alpha = LEARNING_EMA_ALPHA
            # Weighted average: most recent entries have highest weight
            weights = [(1 - alpha) ** (n - 1 - i) * alpha for i in range(n)]
            weights[-1] = 1 - sum(weights[:-1])  # last weight gets remainder
            ratios = [d["ratio"] for d in self.learning_data]
            new_factor = sum(w * r for w, r in zip(weights, ratios))
            new_factor = max(0.3, min(3.0, new_factor))

            _LOGGER.info(
                "AI learning update: predicted=%.2f kWh, actual=%.2f kWh, "
                "ratio=%.3f, new correction_factor=%.3f (was %.3f)",
                predicted_kwh, actual_kwh, ratio, new_factor, self.correction_factor,
            )
            self.correction_factor = round(new_factor, 4)

    def update_consumption_profile(self, hour: int, consumption_kw: float) -> None:
        """Update the hourly consumption profile with new measurement."""
        if consumption_kw < 0 or consumption_kw > 50:
            return
        current = self.consumption_profile.get(hour, DEFAULT_CONSUMPTION_PROFILE.get(hour, 0.3))
        self.consumption_profile[hour] = round(
            (1 - CONSUMPTION_EMA_ALPHA) * current + CONSUMPTION_EMA_ALPHA * consumption_kw, 4
        )

    # ------------------------------------------------------------------
    # Self-consumption / self-sufficiency
    # ------------------------------------------------------------------

    def predict_self_consumption(
        self,
        hourly_production: list[dict[str, Any]],
        date: str | None = None,
    ) -> tuple[float, float]:
        """Predict self-consumption % and self-sufficiency % for a day.

        Returns:
            (self_consumption_pct, self_sufficiency_pct)

        Self-consumption  = self_consumed / production  (how much solar is used directly)
        Self-sufficiency  = self_consumed / consumption (how much consumption is covered by solar)
        """
        if date:
            entries = [e for e in hourly_production if e["time"].startswith(date)]
        else:
            entries = hourly_production

        total_production_kwh = 0.0
        total_consumption_kwh = 0.0
        total_self_consumed_kwh = 0.0

        for entry in entries:
            hour = int(entry["time"][11:13])
            production_kw = entry["power_w"] / 1000.0
            consumption_kw = self.consumption_profile.get(
                hour, DEFAULT_CONSUMPTION_PROFILE.get(hour, 0.30)
            )

            self_consumed_kw = min(production_kw, consumption_kw)
            total_production_kwh += production_kw
            total_consumption_kwh += consumption_kw
            total_self_consumed_kwh += self_consumed_kw

        if total_production_kwh > 0.01:
            self_consumption_pct = (total_self_consumed_kwh / total_production_kwh) * 100
        else:
            self_consumption_pct = 0.0

        if total_consumption_kwh > 0.01:
            self_sufficiency_pct = (total_self_consumed_kwh / total_consumption_kwh) * 100
        else:
            self_sufficiency_pct = 0.0

        return round(min(100.0, self_consumption_pct), 1), round(min(100.0, self_sufficiency_pct), 1)

    # ------------------------------------------------------------------
    # Confidence score
    # ------------------------------------------------------------------

    @property
    def confidence_pct(self) -> int:
        """Return model confidence as percentage (0-100).

        Increases as more learning data is collected.
        Minimum 40% (physics model only), max 95% (well-trained model).
        """
        if self.learning_days == 0:
            return 40
        base = 40
        gain = min(55, self.learning_days * 2)  # +2% per day, up to +55%
        return base + gain

    # ------------------------------------------------------------------
    # Persistence helpers (called by coordinator)
    # ------------------------------------------------------------------

    def get_state_dict(self) -> dict[str, Any]:
        """Serialize learning state for persistence."""
        return {
            "correction_factor": self.correction_factor,
            "learning_data": self.learning_data,
            "consumption_profile": {str(k): v for k, v in self.consumption_profile.items()},
            "learning_days": self.learning_days,
        }

    def restore_state_dict(self, state: dict[str, Any]) -> None:
        """Restore learning state from persistence."""
        self.correction_factor = float(state.get("correction_factor", 1.0))
        self.learning_data = state.get("learning_data", [])
        raw_profile = state.get("consumption_profile", {})
        if raw_profile:
            self.consumption_profile = {int(k): float(v) for k, v in raw_profile.items()}
        self.learning_days = state.get("learning_days", len(self.learning_data))
        _LOGGER.info(
            "Restored AI model state: correction_factor=%.3f, learning_days=%d",
            self.correction_factor,
            self.learning_days,
        )
