"""Constants for Casa ES Energy Manager."""

DOMAIN = "casa_es_energy_manager"
NAME = "Casa ES Energy Manager"
VERSION = "0.2.1"

# Core electrical sensors.
CONF_PV_POWER_SENSOR = "pv_power_sensor"
CONF_LOAD_POWER_SENSOR = "load_power_sensor"
CONF_GRID_POWER_SENSOR = "grid_power_sensor"
CONF_BATTERY_SOC_SENSOR = "battery_soc_sensor"
CONF_BATTERY_POWER_SENSOR = "battery_power_sensor"
CONF_PHASE_L1_POWER_SENSOR = "phase_l1_power_sensor"
CONF_PHASE_L2_POWER_SENSOR = "phase_l2_power_sensor"
CONF_PHASE_L3_POWER_SENSOR = "phase_l3_power_sensor"

# Solar opportunity / forecast sensors.
CONF_PV_POTENTIAL_POWER_SENSOR = "pv_potential_power_sensor"
CONF_PV_FORECAST_REMAINING_TODAY_SENSOR = "pv_forecast_remaining_today_sensor"
CONF_PV_FORECAST_CURRENT_HOUR_SENSOR = "pv_forecast_current_hour_sensor"
CONF_PV_FORECAST_NEXT_HOUR_SENSOR = "pv_forecast_next_hour_sensor"
CONF_PV_FORECAST_TODAY_SENSOR = "pv_forecast_today_sensor"
CONF_PV_FORECAST_TOMORROW_SENSOR = "pv_forecast_tomorrow_sensor"

# Optional contextual data for the AI planner.
CONF_WEATHER_ENTITY = "weather_entity"
CONF_EXTRA_CONTEXT_SENSORS = "extra_context_sensors"

CONF_INVERTER_POWER_LIMIT = "inverter_power_limit"
CONF_PHASE_POWER_LIMIT = "phase_power_limit"
CONF_GRID_POWER_LIMIT = "grid_power_limit"
CONF_SAFETY_MARGIN = "safety_margin"

CONF_AI_ENABLED = "ai_enabled"
CONF_AI_TASK_ENTITY = "ai_task_entity"
CONF_AI_INTERVAL_MINUTES = "ai_interval_minutes"
CONF_BATTERY_CAPACITY_KWH = "battery_capacity_kwh"
CONF_BATTERY_TARGET_SOC = "battery_target_soc"
CONF_BATTERY_TARGET_HOUR = "battery_target_hour"

DEFAULT_INVERTER_POWER_LIMIT = 10_000.0
DEFAULT_PHASE_POWER_LIMIT = 3_000.0
DEFAULT_GRID_POWER_LIMIT = 6_000.0
DEFAULT_SAFETY_MARGIN = 250.0

DEFAULT_AI_ENABLED = False
DEFAULT_AI_INTERVAL_MINUTES = 30
DEFAULT_BATTERY_CAPACITY_KWH = 14.3
DEFAULT_BATTERY_TARGET_SOC = 100.0
DEFAULT_BATTERY_TARGET_HOUR = 17

# Read-only heuristic used only to flag a likely zero-export curtailment condition.
CURTAILMENT_SOC_THRESHOLD = 98.0
CURTAILMENT_POTENTIAL_GAP_W = 400.0
CURTAILMENT_GRID_IMPORT_MAX_W = 150.0

UPDATE_INTERVAL_SECONDS = 5
