"""Constants for Casa ES Energy Manager."""

DOMAIN = "casa_es_energy_manager"
NAME = "Casa ES Energy Manager"
VERSION = "0.1.1"

CONF_PV_POWER_SENSOR = "pv_power_sensor"
CONF_LOAD_POWER_SENSOR = "load_power_sensor"
CONF_GRID_POWER_SENSOR = "grid_power_sensor"
CONF_BATTERY_SOC_SENSOR = "battery_soc_sensor"
CONF_BATTERY_POWER_SENSOR = "battery_power_sensor"
CONF_PHASE_L1_POWER_SENSOR = "phase_l1_power_sensor"
CONF_PHASE_L2_POWER_SENSOR = "phase_l2_power_sensor"
CONF_PHASE_L3_POWER_SENSOR = "phase_l3_power_sensor"

CONF_INVERTER_POWER_LIMIT = "inverter_power_limit"
CONF_PHASE_POWER_LIMIT = "phase_power_limit"
CONF_GRID_POWER_LIMIT = "grid_power_limit"
CONF_SAFETY_MARGIN = "safety_margin"

DEFAULT_INVERTER_POWER_LIMIT = 10_000.0
DEFAULT_PHASE_POWER_LIMIT = 3_000.0
DEFAULT_GRID_POWER_LIMIT = 6_000.0
DEFAULT_SAFETY_MARGIN = 250.0

UPDATE_INTERVAL_SECONDS = 5
