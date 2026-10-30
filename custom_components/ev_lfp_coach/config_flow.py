"""Config flow, reconfigure flow and options flow for EV LFP Battery Coach."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_NAME, PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    CONF_ABORT_AFTER_HOURS,
    CONF_BATTERY_LEVEL_ENTITY,
    CONF_CABLE_ENTITY,
    CONF_CALIBRATION_INTERVAL_DAYS,
    CONF_CALIBRATION_LIMIT,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_CHARGING_POWER_ENTITY,
    CONF_CHARGING_STATUS_ENTITY,
    CONF_DAILY_LIMIT,
    CONF_FINISHED_STATUSES,
    CONF_HOME_ONLY,
    CONF_HOME_ZONE,
    CONF_IDLE_MINUTES,
    CONF_IDLE_POWER_WATTS,
    CONF_LOCATION_ENTITY,
    CONF_MIN_HOLD_MINUTES,
    CONF_NOTIFY_SERVICE,
    CONF_RESTORE_AFTER_MANUAL,
    CONF_SEND_NOTIFICATIONS,
    CONF_TOP_BALANCING_BUFFER_SECONDS,
    CONF_TOP_BALANCING_TIMEOUT_MINUTES,
    DEFAULT_ABORT_AFTER_HOURS,
    DEFAULT_CALIBRATION_INTERVAL_DAYS,
    DEFAULT_CALIBRATION_LIMIT,
    DEFAULT_DAILY_LIMIT,
    DEFAULT_FINISHED_STATUSES,
    DEFAULT_IDLE_MINUTES,
    DEFAULT_IDLE_POWER_WATTS,
    DEFAULT_MIN_HOLD_MINUTES,
    DEFAULT_NAME,
    DEFAULT_RESTORE_AFTER_MANUAL,
    DEFAULT_SEND_NOTIFICATIONS,
    DEFAULT_TOP_BALANCING_BUFFER_SECONDS,
    DEFAULT_TOP_BALANCING_TIMEOUT_MINUTES,
    DOMAIN,
)

VEHICLE_ENTITY_KEYS = (
    CONF_BATTERY_LEVEL_ENTITY,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_CHARGING_STATUS_ENTITY,
    CONF_CABLE_ENTITY,
    CONF_LOCATION_ENTITY,
    CONF_CHARGING_POWER_ENTITY,
)
OPTIONAL_VEHICLE_KEYS = (
    CONF_CABLE_ENTITY,
    CONF_LOCATION_ENTITY,
    CONF_CHARGING_POWER_ENTITY,
    CONF_HOME_ZONE,
)
INT_KEYS = (
    CONF_IDLE_POWER_WATTS,
    CONF_IDLE_MINUTES,
    CONF_DAILY_LIMIT,
    CONF_CALIBRATION_LIMIT,
    CONF_CALIBRATION_INTERVAL_DAYS,
    CONF_MIN_HOLD_MINUTES,
    CONF_TOP_BALANCING_TIMEOUT_MINUTES,
    CONF_TOP_BALANCING_BUFFER_SECONDS,
    CONF_ABORT_AFTER_HOURS,
)
KNOWN_FINISHED_STATUSES = [
    "complete",
    "completed",
    "stopped",
    "disconnected",
    "no_power",
    "finished",
    "idle",
    "not_charging",
    "off",
]
SECTION_OPTIONAL = "optional"
SECTION_OPTIONAL_OPEN = "_optional_open"  # form-only hint, never stored
NOT_NOTIFY_SERVICES = frozenset({"send_message", "persistent_notification"})


def _slider(
    minimum: int, maximum: int, step: int, unit: str, *, box: bool = False
) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX if box else selector.NumberSelectorMode.SLIDER,
        )
    )


def _entity_selector(*domains: str) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=list(domains)))


def _vehicle_schema(values: Mapping[str, Any]) -> vol.Schema:
    """Schema to pick the vehicle entities."""

    def suggested(key: str) -> dict[str, Any]:
        return {"description": {"suggested_value": values.get(key)}}

    return vol.Schema(
        {
            vol.Optional(CONF_NAME, **suggested(CONF_NAME)): selector.TextSelector(),
            vol.Required(
                CONF_BATTERY_LEVEL_ENTITY, **suggested(CONF_BATTERY_LEVEL_ENTITY)
            ): _entity_selector("sensor"),
            vol.Required(
                CONF_CHARGE_LIMIT_ENTITY, **suggested(CONF_CHARGE_LIMIT_ENTITY)
            ): _entity_selector("number"),
            vol.Required(
                CONF_CHARGING_STATUS_ENTITY, **suggested(CONF_CHARGING_STATUS_ENTITY)
            ): _entity_selector("sensor", "binary_sensor"),
            vol.Required(SECTION_OPTIONAL): section(
                vol.Schema(
                    {
                        vol.Optional(
                            CONF_CABLE_ENTITY, **suggested(CONF_CABLE_ENTITY)
                        ): _entity_selector("binary_sensor"),
                        vol.Optional(
                            CONF_CHARGING_POWER_ENTITY, **suggested(CONF_CHARGING_POWER_ENTITY)
                        ): _entity_selector("sensor"),
                        vol.Optional(
                            CONF_HOME_ONLY, default=bool(values.get(CONF_HOME_ONLY, False))
                        ): selector.BooleanSelector(),
                        vol.Optional(
                            CONF_LOCATION_ENTITY, **suggested(CONF_LOCATION_ENTITY)
                        ): _entity_selector("device_tracker", "person"),
                        vol.Optional(CONF_HOME_ZONE, **suggested(CONF_HOME_ZONE)): _entity_selector(
                            "zone"
                        ),
                    }
                ),
                {"collapsed": not values.get(SECTION_OPTIONAL_OPEN, False)},
            ),
        }
    )


def _flatten_vehicle(hass: HomeAssistant, user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Merge the collapsible "optional" section and fill in a blank name."""
    data = {key: value for key, value in user_input.items() if key != SECTION_OPTIONAL}
    data.update(user_input.get(SECTION_OPTIONAL) or {})
    name = data.get(CONF_NAME)
    data[CONF_NAME] = (
        name.strip()
        if isinstance(name, str) and name.strip()
        else _vehicle_name(hass, data.get(CONF_BATTERY_LEVEL_ENTITY))
    )
    return data


def _vehicle_name(hass: HomeAssistant, battery_entity_id: str | None) -> str:
    """Name the coach after the device that owns the battery sensor."""
    entry = er.async_get(hass).async_get(battery_entity_id) if battery_entity_id else None
    device = dr.async_get(hass).async_get(entry.device_id) if entry and entry.device_id else None
    return (device and (device.name_by_user or device.name)) or DEFAULT_NAME


def _notify_options(hass: HomeAssistant) -> list[str]:
    """Return the notify entities and legacy notify services that exist."""
    entities = {state.entity_id for state in hass.states.async_all("notify")}
    services = {
        f"notify.{name}"
        for name in hass.services.async_services_for_domain("notify")
        if name not in NOT_NOTIFY_SERVICES
    }
    return sorted(entities | services)


def _parameters_schema(hass: HomeAssistant, values: Mapping[str, Any]) -> vol.Schema:
    """Schema for the tunable parameters, shared by setup and options."""
    return vol.Schema(
        {
            vol.Required(
                CONF_DAILY_LIMIT, default=values.get(CONF_DAILY_LIMIT, DEFAULT_DAILY_LIMIT)
            ): _slider(50, 90, 1, PERCENTAGE),
            vol.Required(
                CONF_CALIBRATION_LIMIT,
                default=values.get(CONF_CALIBRATION_LIMIT, DEFAULT_CALIBRATION_LIMIT),
            ): _slider(90, 100, 1, PERCENTAGE),
            vol.Required(
                CONF_CALIBRATION_INTERVAL_DAYS,
                default=values.get(
                    CONF_CALIBRATION_INTERVAL_DAYS, DEFAULT_CALIBRATION_INTERVAL_DAYS
                ),
            ): _slider(1, 60, 1, UnitOfTime.DAYS, box=True),
            vol.Required(
                CONF_MIN_HOLD_MINUTES,
                default=values.get(CONF_MIN_HOLD_MINUTES, DEFAULT_MIN_HOLD_MINUTES),
            ): _slider(0, 60, 5, "min"),
            vol.Required(
                CONF_TOP_BALANCING_TIMEOUT_MINUTES,
                default=values.get(
                    CONF_TOP_BALANCING_TIMEOUT_MINUTES, DEFAULT_TOP_BALANCING_TIMEOUT_MINUTES
                ),
            ): _slider(10, 180, 5, "min"),
            vol.Required(
                CONF_TOP_BALANCING_BUFFER_SECONDS,
                default=values.get(
                    CONF_TOP_BALANCING_BUFFER_SECONDS, DEFAULT_TOP_BALANCING_BUFFER_SECONDS
                ),
            ): _slider(0, 300, 10, "s"),
            vol.Required(
                CONF_IDLE_POWER_WATTS,
                default=values.get(CONF_IDLE_POWER_WATTS, DEFAULT_IDLE_POWER_WATTS),
            ): _slider(0, 500, 10, "W"),
            vol.Required(
                CONF_IDLE_MINUTES,
                default=values.get(CONF_IDLE_MINUTES, DEFAULT_IDLE_MINUTES),
            ): _slider(1, 30, 1, "min"),
            vol.Required(
                CONF_FINISHED_STATUSES,
                default=values.get(CONF_FINISHED_STATUSES, DEFAULT_FINISHED_STATUSES),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=KNOWN_FINISHED_STATUSES,
                    multiple=True,
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_ABORT_AFTER_HOURS,
                default=values.get(CONF_ABORT_AFTER_HOURS, DEFAULT_ABORT_AFTER_HOURS),
            ): _slider(0, 72, 1, UnitOfTime.HOURS, box=True),
            vol.Required(
                CONF_RESTORE_AFTER_MANUAL,
                default=values.get(CONF_RESTORE_AFTER_MANUAL, DEFAULT_RESTORE_AFTER_MANUAL),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_SEND_NOTIFICATIONS,
                default=values.get(CONF_SEND_NOTIFICATIONS, DEFAULT_SEND_NOTIFICATIONS),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_NOTIFY_SERVICE,
                description={"suggested_value": values.get(CONF_NOTIFY_SERVICE)},
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_notify_options(hass),
                    custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _normalize_parameters(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Cast selector output to plain types so the stored config is stable."""
    data = dict(user_input)
    for key in INT_KEYS:
        if key in data:
            data[key] = int(data[key])
    if CONF_FINISHED_STATUSES in data:
        data[CONF_FINISHED_STATUSES] = [
            str(item).strip().lower() for item in data[CONF_FINISHED_STATUSES] if str(item).strip()
        ]
    # An optional field the user cleared is simply absent: store None so it
    # overrides the value saved at setup time.
    notify = data.get(CONF_NOTIFY_SERVICE)
    data[CONF_NOTIFY_SERVICE] = (
        notify.strip() if isinstance(notify, str) and notify.strip() else None
    )
    return data


def _validate_parameters(hass: HomeAssistant, data: Mapping[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if data[CONF_DAILY_LIMIT] >= data[CONF_CALIBRATION_LIMIT]:
        errors["base"] = "daily_limit_greater_than_calibration"
    elif data[CONF_MIN_HOLD_MINUTES] > data[CONF_TOP_BALANCING_TIMEOUT_MINUTES]:
        errors["base"] = "min_hold_exceeds_timeout"
    elif not data[CONF_FINISHED_STATUSES]:
        errors[CONF_FINISHED_STATUSES] = "no_finished_statuses"
    elif (target := data.get(CONF_NOTIFY_SERVICE)) and target not in _notify_options(hass):
        errors[CONF_NOTIFY_SERVICE] = "invalid_notify_target"
    return errors


def _validate_vehicle(data: Mapping[str, Any]) -> dict[str, str]:
    chosen = [data[key] for key in VEHICLE_ENTITY_KEYS if data.get(key)]
    if len(chosen) != len(set(chosen)):
        return {"base": "duplicate_entities"}
    if data.get(CONF_HOME_ONLY) and not data.get(CONF_LOCATION_ENTITY):
        return {"base": "location_required"}
    return {}


class EvLfpConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup of a vehicle."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the vehicle entities."""
        errors: dict[str, str] = {}
        values: dict[str, Any] = {}
        if user_input is not None:
            values = _flatten_vehicle(self.hass, user_input)
            errors = _validate_vehicle(values)
            if not errors:
                # One coach per charge limit: two would fight over the same entity.
                await self.async_set_unique_id(values[CONF_CHARGE_LIMIT_ENTITY])
                self._abort_if_unique_id_configured()
                self._data.update(values)
                return await self.async_step_parameters()
            values[SECTION_OPTIONAL_OPEN] = True

        return self.async_show_form(
            step_id="user",
            data_schema=_vehicle_schema(values),
            errors=errors,
        )

    async def async_step_parameters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose limits, timings and notifications."""
        errors: dict[str, str] = {}
        values: Mapping[str, Any] = {}
        if user_input is not None:
            values = user_input
            data = _normalize_parameters(user_input)
            errors = _validate_parameters(self.hass, data)
            if not errors:
                self._data.update(data)
                return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)

        return self.async_show_form(
            step_id="parameters",
            data_schema=_parameters_schema(self.hass, values),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point the coach at different vehicle entities, keeping its history."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        values: dict[str, Any] = {**entry.data, **entry.options}
        if user_input is not None:
            values = _flatten_vehicle(self.hass, user_input)
            errors = _validate_vehicle(values)
            new_id = values[CONF_CHARGE_LIMIT_ENTITY]
            if not errors and any(
                other.unique_id == new_id
                for other in self._async_current_entries()
                if other.entry_id != entry.entry_id
            ):
                errors["base"] = "already_configured"
            if not errors:
                cleaned = {key: values.get(key) for key in OPTIONAL_VEHICLE_KEYS}
                cleaned[CONF_HOME_ONLY] = bool(values.get(CONF_HOME_ONLY))
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=new_id,
                    title=values[CONF_NAME],
                    data={**entry.data, **values, **cleaned},
                )
        values[SECTION_OPTIONAL_OPEN] = True

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_vehicle_schema(values),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EvLfpOptionsFlow:
        """Return the options flow."""
        return EvLfpOptionsFlow()


class EvLfpOptionsFlow(OptionsFlowWithReload):
    """Change the parameters at any time; the entry reloads automatically."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the parameters."""
        errors: dict[str, str] = {}
        values: Mapping[str, Any] = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            values = user_input
            data = _normalize_parameters(user_input)
            errors = _validate_parameters(self.hass, data)
            if not errors:
                return self.async_create_entry(data=data)

        return self.async_show_form(
            step_id="init",
            data_schema=_parameters_schema(self.hass, values),
            errors=errors,
        )
