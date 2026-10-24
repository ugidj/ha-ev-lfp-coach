"""Constants for the EV LFP Battery Coach integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ev_lfp_coach"

# Vehicle entity keys (config entry data)
CONF_BATTERY_LEVEL_ENTITY: Final = "battery_level_entity"
CONF_CHARGE_LIMIT_ENTITY: Final = "charge_limit_entity"
CONF_CHARGING_STATUS_ENTITY: Final = "charging_status_entity"
CONF_CABLE_ENTITY: Final = "cable_entity"
CONF_LOCATION_ENTITY: Final = "location_entity"
CONF_CHARGING_POWER_ENTITY: Final = "charging_power_entity"
CONF_HOME_ZONE: Final = "home_zone"
CONF_HOME_ONLY: Final = "home_only"

# Parameter keys (config entry data, overridden by options)
CONF_DAILY_LIMIT: Final = "daily_limit"
CONF_CALIBRATION_LIMIT: Final = "calibration_limit"
CONF_CALIBRATION_INTERVAL_DAYS: Final = "calibration_interval_days"
CONF_MIN_HOLD_MINUTES: Final = "min_hold_minutes"
CONF_TOP_BALANCING_TIMEOUT_MINUTES: Final = "top_balancing_timeout_minutes"
CONF_TOP_BALANCING_BUFFER_SECONDS: Final = "top_balancing_buffer_seconds"
CONF_IDLE_POWER_WATTS: Final = "idle_power_watts"
CONF_IDLE_MINUTES: Final = "idle_minutes"
CONF_FINISHED_STATUSES: Final = "finished_statuses"
CONF_ABORT_AFTER_HOURS: Final = "abort_after_hours"
CONF_RESTORE_AFTER_MANUAL: Final = "restore_after_manual"
CONF_SEND_NOTIFICATIONS: Final = "send_notifications"
CONF_NOTIFY_SERVICE: Final = "notify_service"

# Defaults
DEFAULT_NAME: Final = "Electric vehicle"
DEFAULT_DAILY_LIMIT: Final = 75
DEFAULT_CALIBRATION_LIMIT: Final = 100
DEFAULT_CALIBRATION_INTERVAL_DAYS: Final = 14
DEFAULT_MIN_HOLD_MINUTES: Final = 30
DEFAULT_HOME_ONLY: Final = False
DEFAULT_TOP_BALANCING_TIMEOUT_MINUTES: Final = 45
DEFAULT_TOP_BALANCING_BUFFER_SECONDS: Final = 30
DEFAULT_IDLE_POWER_WATTS: Final = 50
DEFAULT_IDLE_MINUTES: Final = 3
DEFAULT_FINISHED_STATUSES: Final = ["complete", "stopped", "disconnected", "no_power"]
DEFAULT_ABORT_AFTER_HOURS: Final = 12
DEFAULT_RESTORE_AFTER_MANUAL: Final = True
DEFAULT_SEND_NOTIFICATIONS: Final = True
DEFAULT_HOME_ZONE: Final = "zone.home"

# Charging status values meaning "the cable is not plugged in". Only used when
# no dedicated cable sensor is configured.
UNPLUGGED_STATUSES: Final = frozenset({"disconnected"})
# Cable sensor states
CABLE_CONNECTED_STATES: Final = frozenset(
    {"on", "true", "connected", "plugged", "plugged_in", "yes", "1"}
)
CABLE_DISCONNECTED_STATES: Final = frozenset(
    {"off", "false", "disconnected", "unplugged", "no", "0"}
)

# Workflow states (also the options of the calibration state sensor)
STATE_CALIBRATED: Final = "calibrated"
STATE_CALIBRATION_DUE: Final = "calibration_due"
STATE_CALIBRATING_100: Final = "calibrating_100"
STATE_TOP_BALANCING: Final = "top_balancing"
CALIBRATION_STATES: Final = [
    STATE_CALIBRATED,
    STATE_CALIBRATION_DUE,
    STATE_CALIBRATING_100,
    STATE_TOP_BALANCING,
]

# Reasons reported in events, notifications and history
REASON_STATUS: Final = "status"
REASON_POWER: Final = "power"
REASON_TIMEOUT: Final = "timeout"
REASON_UNPLUGGED: Final = "unplugged"
REASON_MANUAL: Final = "manual"
REASON_AWAY: Final = "away"

# Events fired on the bus
EVENT_CALIBRATION_STARTED: Final = f"{DOMAIN}_calibration_started"
EVENT_BALANCING_STARTED: Final = f"{DOMAIN}_balancing_started"
EVENT_CALIBRATION_COMPLETED: Final = f"{DOMAIN}_calibration_completed"
EVENT_CALIBRATION_ABORTED: Final = f"{DOMAIN}_calibration_aborted"

# Services
SERVICE_START_CALIBRATION: Final = "start_calibration"
SERVICE_CANCEL_CALIBRATION: Final = "cancel_calibration"
SERVICE_MARK_CALIBRATED: Final = "mark_calibrated"
ATTR_TIMESTAMP: Final = "timestamp"

# Behaviour tuning
STATE_CHANGE_DEBOUNCE_SECONDS: Final = 2.0
UPDATE_INTERVAL_MINUTES: Final = 10
REACH_TOLERANCE: Final = 0.5
COMMAND_CONFIRM_GRACE_SECONDS: Final = 300
COMMAND_RETRY_SECONDS: Final = 300
COMMAND_TIMEOUT_SECONDS: Final = 60
SNOOZE_HOURS: Final = 24
FAILURES_BEFORE_ISSUE: Final = 3
HISTORY_LENGTH: Final = 10

# Storage
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}.storage"

# Repairs
ISSUE_SET_LIMIT_FAILED: Final = "set_limit_failed"
ISSUE_ENTITY_MISSING: Final = "entity_missing"
