"""Constants for Solar Forecast AI integration."""

DOMAIN = "solar_forecast_ai"
PLATFORM = "sensor"

# Configuration keys
CONF_PEAK_POWER = "peak_power"
CONF_PERFORMANCE_RATIO = "performance_ratio"
CONF_PRODUCTION_SENSOR = "production_sensor"
CONF_CONSUMPTION_SENSOR = "consumption_sensor"
CONF_FORECAST_DAYS = "forecast_days"

# Defaults
DEFAULT_PERFORMANCE_RATIO = 0.80
DEFAULT_FORECAST_DAYS = 3
DEFAULT_PEAK_POWER = 5.0

# API
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 30

# Update interval
UPDATE_INTERVAL_HOURS = 1

# Learning algorithm parameters
MAX_LEARNING_DAYS = 30
MIN_LEARNING_DAYS = 3
LEARNING_EMA_ALPHA = 0.15
CONSUMPTION_EMA_ALPHA = 0.05

# Storage
STORAGE_KEY = f"{DOMAIN}_learning_data"
STORAGE_VERSION = 1

# Sensor unique ID suffixes
SENSOR_FORECAST_TODAY = "forecast_today"
SENSOR_FORECAST_TOMORROW = "forecast_tomorrow"
SENSOR_FORECAST_7DAYS = "forecast_7days"
SENSOR_FORECAST_POWER_NOW = "forecast_power_now"
SENSOR_PEAK_POWER_TODAY = "peak_power_today"
SENSOR_PEAK_TIME_TODAY = "peak_time_today"
SENSOR_SELF_CONSUMPTION_TODAY = "self_consumption_today"
SENSOR_SELF_SUFFICIENCY_TODAY = "self_sufficiency_today"
SENSOR_CORRECTION_FACTOR = "correction_factor"
SENSOR_FORECAST_TOMORROW_PEAK = "forecast_tomorrow_peak"

# Attribution
ATTRIBUTION = "Gegevens van Open-Meteo (open-meteo.com)"
