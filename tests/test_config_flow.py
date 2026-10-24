"""Tests for the config, reconfigure and options flows."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.ev_lfp_coach.const import (
    CONF_ABORT_AFTER_HOURS,
    CONF_BATTERY_LEVEL_ENTITY,
    CONF_CABLE_ENTITY,
    CONF_CALIBRATION_INTERVAL_DAYS,
    CONF_CALIBRATION_LIMIT,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_CHARGING_STATUS_ENTITY,
    CONF_DAILY_LIMIT,
    CONF_FINISHED_STATUSES,
    CONF_HOME_ONLY,
    CONF_LOCATION_ENTITY,
    CONF_MIN_HOLD_MINUTES,
    CONF_NOTIFY_SERVICE,
    CONF_RESTORE_AFTER_MANUAL,
    CONF_SEND_NOTIFICATIONS,
    CONF_TOP_BALANCING_BUFFER_SECONDS,
    CONF_TOP_BALANCING_TIMEOUT_MINUTES,
    DOMAIN,
)

from .conftest import (
    BASE_DATA,
    BATTERY,
    CABLE,
    LIMIT,
    LOCATION,
    STATUS,
    make_entry,
    set_vehicle,
    setup_entry,
)

VEHICLE_INPUT: dict[str, Any] = {
    CONF_NAME: "Model 3",
    CONF_BATTERY_LEVEL_ENTITY: BATTERY,
    CONF_CHARGE_LIMIT_ENTITY: LIMIT,
    CONF_CHARGING_STATUS_ENTITY: STATUS,
    "optional": {
        CONF_CABLE_ENTITY: CABLE,
        CONF_HOME_ONLY: True,
        CONF_LOCATION_ENTITY: LOCATION,
    },
}
PARAMETER_INPUT: dict[str, Any] = {
    CONF_DAILY_LIMIT: 75,
    CONF_CALIBRATION_LIMIT: 100,
    CONF_CALIBRATION_INTERVAL_DAYS: 14,
    CONF_MIN_HOLD_MINUTES: 10,
    CONF_TOP_BALANCING_TIMEOUT_MINUTES: 45,
    CONF_TOP_BALANCING_BUFFER_SECONDS: 30,
    CONF_FINISHED_STATUSES: ["complete", "stopped"],
    CONF_ABORT_AFTER_HOURS: 12,
    CONF_RESTORE_AFTER_MANUAL: True,
    CONF_SEND_NOTIFICATIONS: True,
}


async def _start(hass: HomeAssistant, vehicle: dict[str, Any] | None = None) -> Any:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], vehicle or VEHICLE_INPUT
    )


async def test_full_flow_creates_the_entry(hass: HomeAssistant) -> None:
    """Vehicle step, then parameters, then an entry with everything in data."""
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "parameters"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], PARAMETER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Model 3"
    assert result["data"][CONF_CHARGE_LIMIT_ENTITY] == LIMIT
    assert result["data"][CONF_DAILY_LIMIT] == 75
    assert result["data"][CONF_FINISHED_STATUSES] == ["complete", "stopped"]
    assert result["data"][CONF_NOTIFY_SERVICE] is None
    assert result["result"].unique_id == LIMIT


async def test_same_charge_limit_cannot_be_added_twice(hass: HomeAssistant) -> None:
    """Two coaches on one charge limit entity would fight each other."""
    make_entry().add_to_hass(hass)

    result = await _start(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_same_entity_in_two_fields_is_rejected(hass: HomeAssistant) -> None:
    """A copy-paste mistake is caught before it does anything."""
    result = await _start(hass, {**VEHICLE_INPUT, CONF_CHARGING_STATUS_ENTITY: BATTERY})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "duplicate_entities"}


async def test_optional_section_can_be_left_empty(hass: HomeAssistant) -> None:
    """Only battery, limit and status are needed; nothing else is asked."""
    minimal = {key: value for key, value in VEHICLE_INPUT.items() if key != "optional"} | {
        "optional": {}
    }

    result = await _start(hass, minimal)
    assert result["step_id"] == "parameters"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], PARAMETER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"].get(CONF_CABLE_ENTITY) is None
    assert result["data"].get(CONF_LOCATION_ENTITY) is None
    assert not result["data"].get(CONF_HOME_ONLY)


async def test_home_only_needs_a_location(hass: HomeAssistant) -> None:
    """Restricting to home without saying where home is makes no sense."""
    vehicle = {**VEHICLE_INPUT, "optional": {CONF_HOME_ONLY: True}}

    result = await _start(hass, vehicle)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "location_required"}


async def test_daily_limit_must_be_below_calibration_limit(hass: HomeAssistant) -> None:
    """Equal limits would make the calibration pointless."""
    result = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**PARAMETER_INPUT, CONF_DAILY_LIMIT: 90, CONF_CALIBRATION_LIMIT: 90},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "daily_limit_greater_than_calibration"}


async def test_min_hold_cannot_exceed_timeout(hass: HomeAssistant) -> None:
    """Holding longer than the safety timeout is contradictory."""
    result = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**PARAMETER_INPUT, CONF_MIN_HOLD_MINUTES: 60, CONF_TOP_BALANCING_TIMEOUT_MINUTES: 30},
    )

    assert result["errors"] == {"base": "min_hold_exceeds_timeout"}


async def test_unknown_notify_target_is_rejected(hass: HomeAssistant) -> None:
    """Typos in the notify target are caught up front."""
    result = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**PARAMETER_INPUT, CONF_NOTIFY_SERVICE: "notify.nope"}
    )

    assert result["errors"] == {CONF_NOTIFY_SERVICE: "invalid_notify_target"}


async def test_existing_notify_service_is_accepted(hass: HomeAssistant) -> None:
    """Legacy notify services are valid targets."""
    async_mock_service(hass, "notify", "mobile_app_phone")
    result = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**PARAMETER_INPUT, CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NOTIFY_SERVICE] == "notify.mobile_app_phone"


async def test_options_flow_can_clear_the_notify_target(hass: HomeAssistant) -> None:
    """A field the user empties must stop applying (it used to resurrect)."""
    async_mock_service(hass, "notify", "mobile_app_phone")
    set_vehicle(hass, cable="off")
    entry = make_entry(data={CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"})
    entry, coordinator = await setup_entry(hass, entry)
    assert coordinator.notify_service == "notify.mobile_app_phone"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**PARAMETER_INPUT, CONF_DAILY_LIMIT: 80}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options[CONF_NOTIFY_SERVICE] is None
    assert entry.runtime_data is not coordinator  # reloaded
    assert entry.runtime_data.notify_service is None
    assert entry.runtime_data.daily_limit == 80


async def test_options_flow_validates(hass: HomeAssistant) -> None:
    """The same rules as in the setup apply to the options."""
    set_vehicle(hass, cable="off")
    entry, _ = await setup_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**PARAMETER_INPUT, CONF_DAILY_LIMIT: 90, CONF_CALIBRATION_LIMIT: 90},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "daily_limit_greater_than_calibration"}


async def test_reconfigure_swaps_entities_and_keeps_history(hass: HomeAssistant) -> None:
    """After changing integration the entry can follow the new entities."""
    set_vehicle(hass, cable="off")
    entry, _ = await setup_entry(hass)
    new_limit = "number.new_car_charge_limit"

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "New car",
            CONF_BATTERY_LEVEL_ENTITY: "sensor.new_car_battery",
            CONF_CHARGE_LIMIT_ENTITY: new_limit,
            CONF_CHARGING_STATUS_ENTITY: "sensor.new_car_charging",
            "optional": {CONF_HOME_ONLY: False},
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == new_limit
    assert entry.title == "New car"
    assert entry.data[CONF_CHARGE_LIMIT_ENTITY] == new_limit
    assert entry.data[CONF_CABLE_ENTITY] is None  # cleared
    assert entry.data[CONF_DAILY_LIMIT] == 75  # untouched
    assert entry.runtime_data.charge_limit_entity == new_limit
    assert entry.runtime_data.cable_entity is None


async def test_reconfigure_cannot_steal_another_vehicles_limit(hass: HomeAssistant) -> None:
    """Reconfiguring into an already managed charge limit is refused."""
    MockConfigEntry(
        domain=DOMAIN, entry_id="other", unique_id="number.other_limit", data=BASE_DATA
    ).add_to_hass(hass)
    mine = MockConfigEntry(domain=DOMAIN, entry_id="mine", unique_id="number.mine", data=BASE_DATA)
    mine.add_to_hass(hass)

    result = await mine.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**VEHICLE_INPUT, CONF_CHARGE_LIMIT_ENTITY: "number.other_limit"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "already_configured"}
