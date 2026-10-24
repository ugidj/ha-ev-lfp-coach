"""Tests for setup, entities, services, diagnostics and removal."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.ev_lfp_coach.const import (
    ATTR_TIMESTAMP,
    CONF_CALIBRATION_LIMIT,
    CONF_DAILY_LIMIT,
    CONF_NOTIFY_SERVICE,
    DOMAIN,
    ISSUE_SET_LIMIT_FAILED,
    SERVICE_CANCEL_CALIBRATION,
    SERVICE_MARK_CALIBRATED,
    SERVICE_START_CALIBRATION,
    STATE_CALIBRATED,
    STATE_CALIBRATING_100,
    STATE_CALIBRATION_DUE,
)
from custom_components.ev_lfp_coach.diagnostics import async_get_config_entry_diagnostics

from .conftest import ENTRY_ID, STORAGE, make_entry, set_vehicle, setup_entry

STATE_SENSOR = "sensor.test_car_calibration_state"


def _recent(days: float) -> str:
    return (dt_util.utcnow() - timedelta(days=days)).isoformat()


async def test_setup_unload_and_reload(hass: HomeAssistant) -> None:
    """The entry loads, unloads and loads again without leaking."""
    set_vehicle(hass, cable="off")
    entry, _ = await setup_entry(hass)
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_entities_are_created_with_stable_unique_ids(hass: HomeAssistant) -> None:
    """All platforms register their entities, keyed by entry id."""
    set_vehicle(hass, cable="off")
    entry, _ = await setup_entry(hass)

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    unique_ids = {e.unique_id for e in entries}

    assert unique_ids == {
        f"{ENTRY_ID}_{key}"
        for key in (
            "days_since_calibration",
            "calibration_state",
            "last_calibration",
            "next_calibration",
            "calibration_due",
            "calibration_active",
            "force_calibration",
            "cancel_calibration",
            "reset_calibration_timer",
            "daily_limit",
            "calibration_limit",
            "calibration_interval_days",
            "auto_mode",
        )
    }


async def test_sensors_before_and_after_a_calibration(hass: HomeAssistant, stored: Any) -> None:
    """Never calibrated: unknown days, due. Calibrated a week ago: 7 days."""
    set_vehicle(hass, cable="off")
    await setup_entry(hass)
    assert hass.states.get("sensor.test_car_days_since_calibration").state == "unknown"
    assert hass.states.get("sensor.test_car_next_calibration").state == "unknown"
    assert hass.states.get(STATE_SENSOR).state == STATE_CALIBRATION_DUE
    assert hass.states.get("binary_sensor.test_car_calibration_due").state == "on"
    assert hass.states.get("binary_sensor.test_car_calibration_in_progress").state == "off"
    assert hass.states.get("switch.test_car_automatic_management").state == "on"


async def test_sensors_with_a_recent_calibration(hass: HomeAssistant, stored: Any) -> None:
    """Values come from the persisted calibration."""
    stored({"last_100": _recent(7)})
    set_vehicle(hass, cable="off")
    await setup_entry(hass)

    assert float(hass.states.get("sensor.test_car_days_since_calibration").state) == 7.0
    assert hass.states.get("sensor.test_car_last_calibration").state != "unknown"
    assert hass.states.get("sensor.test_car_next_calibration").state != "unknown"
    assert hass.states.get(STATE_SENSOR).state == STATE_CALIBRATED
    assert hass.states.get("binary_sensor.test_car_calibration_due").state == "off"


async def test_state_sensor_exposes_the_workflow(hass: HomeAssistant) -> None:
    """While calibrating the state, active flag and attributes reflect it."""
    set_vehicle(hass, soc=80, limit=75)
    await setup_entry(hass)

    state = hass.states.get(STATE_SENSOR)
    assert state.state == STATE_CALIBRATING_100
    assert state.attributes["calibration_limit"] == 100
    assert state.attributes["daily_limit"] == 75
    assert hass.states.get("binary_sensor.test_car_calibration_in_progress").state == "on"


async def test_buttons(
    hass: HomeAssistant, stored: Any, set_value_calls: list[ServiceCall]
) -> None:
    """Calibrate-on-next-charge, cancel and mark-calibrated buttons."""
    stored({"last_100": _recent(1)})
    set_vehicle(hass, soc=80, limit=75)
    _, coordinator = await setup_entry(hass)
    assert coordinator.state == STATE_CALIBRATED

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.test_car_calibrate_on_next_charge"}, blocking=True
    )
    assert coordinator.state == STATE_CALIBRATING_100

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.test_car_cancel_calibration"}, blocking=True
    )
    assert [c.data["value"] for c in set_value_calls] == [100, 75]
    assert coordinator.phase is None

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.test_car_mark_calibrated_now"}, blocking=True
    )
    assert coordinator.days_since_last_calibration == 0.0


@pytest.mark.real_number
async def test_numbers_persist_in_options_without_reloading(hass: HomeAssistant) -> None:
    """Dashboard sliders write the options in place; the entry is not reloaded."""
    set_vehicle(hass, cable="off")
    entry, coordinator = await setup_entry(hass)
    assert hass.states.get("number.test_car_daily_charge_limit").state == "75"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.test_car_daily_charge_limit", "value": 80},
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.test_car_calibration_interval", "value": 21},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert entry.options[CONF_DAILY_LIMIT] == 80
    assert entry.runtime_data is coordinator
    assert coordinator.calibration_interval_days == 21
    assert hass.states.get("number.test_car_daily_charge_limit").state == "80"


@pytest.mark.real_number
async def test_numbers_reject_inconsistent_limits(hass: HomeAssistant) -> None:
    """Daily limit and calibration limit must stay ordered."""
    set_vehicle(hass, cable="off")
    entry, coordinator = await setup_entry(hass)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.test_car_daily_charge_limit", "value": 90},
        blocking=True,
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.test_car_calibration_charge_limit", "value": 90},
            blocking=True,
        )

    assert CONF_CALIBRATION_LIMIT not in entry.options
    assert coordinator.calibration_limit == 100


async def test_switch_pauses_the_coach_and_persists(
    hass: HomeAssistant, hass_storage: dict[str, Any], set_value_calls: list[ServiceCall]
) -> None:
    """Off means no commands, and the choice survives a restart."""
    set_vehicle(hass, cable="off")
    entry, coordinator = await setup_entry(hass)

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.test_car_automatic_management"}, blocking=True
    )
    assert not coordinator.enabled
    assert hass_storage[STORAGE]["data"]["enabled"] is False

    set_vehicle(hass, soc=80, limit=75)
    await coordinator.async_refresh()
    assert not set_value_calls

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert not entry.runtime_data.enabled
    assert hass.states.get("switch.test_car_automatic_management").state == "off"


async def test_entity_services(
    hass: HomeAssistant, stored: Any, set_value_calls: list[ServiceCall]
) -> None:
    """start_calibration / cancel_calibration / mark_calibrated target the state sensor."""
    stored({"last_100": _recent(1)})
    set_vehicle(hass, soc=80, limit=75)
    _, coordinator = await setup_entry(hass)

    await hass.services.async_call(
        DOMAIN, SERVICE_START_CALIBRATION, {"entity_id": STATE_SENSOR}, blocking=True
    )
    assert coordinator.state == STATE_CALIBRATING_100

    await hass.services.async_call(
        DOMAIN, SERVICE_CANCEL_CALIBRATION, {"entity_id": STATE_SENSOR}, blocking=True
    )
    assert coordinator.phase is None
    assert [c.data["value"] for c in set_value_calls] == [100, 75]

    when = dt_util.utcnow() - timedelta(days=3)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_MARK_CALIBRATED,
        {"entity_id": STATE_SENSOR, ATTR_TIMESTAMP: when},
        blocking=True,
    )
    assert coordinator.days_since_last_calibration == pytest.approx(3.0, abs=0.1)


async def test_diagnostics_redact_the_notify_target(hass: HomeAssistant) -> None:
    """Diagnostics can be shared publicly."""
    async_mock_service(hass, "notify", "mobile_app_phone")
    set_vehicle(hass, cable="off")
    entry, _ = await setup_entry(
        hass, make_entry(data={CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"})
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["data"][CONF_NOTIFY_SERVICE] == "**REDACTED**"
    assert diagnostics["coordinator"]["snapshot"]["soc"] == 50.0
    assert diagnostics["coordinator"]["state"] == STATE_CALIBRATION_DUE


async def test_removing_the_entry_deletes_storage_and_issues(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Nothing is left behind when the user deletes the integration."""
    set_vehicle(hass, cable="off")
    entry, coordinator = await setup_entry(hass)
    await coordinator.async_force_calibration()
    assert STORAGE in hass_storage
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_SET_LIMIT_FAILED}_{ENTRY_ID}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_SET_LIMIT_FAILED,
    )

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert STORAGE not in hass_storage
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_SET_LIMIT_FAILED}_{ENTRY_ID}") is None
    )
