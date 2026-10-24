"""Shared fixtures and helpers."""

from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta
from typing import Any

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

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
    STORAGE_KEY,
)
from custom_components.ev_lfp_coach.coordinator import EvLfpCoordinator

BATTERY = "sensor.car_battery"
LIMIT = "number.car_charge_limit"
STATUS = "sensor.car_charging"
CABLE = "binary_sensor.car_cable"
LOCATION = "device_tracker.car"
POWER = "sensor.car_power"
ENTRY_ID = "test_entry"
STORAGE = f"{STORAGE_KEY}.{ENTRY_ID}"

BASE_DATA: dict[str, Any] = {
    CONF_NAME: "Test Car",
    CONF_BATTERY_LEVEL_ENTITY: BATTERY,
    CONF_CHARGE_LIMIT_ENTITY: LIMIT,
    CONF_CHARGING_STATUS_ENTITY: STATUS,
    CONF_CABLE_ENTITY: CABLE,
    CONF_LOCATION_ENTITY: LOCATION,
    CONF_HOME_ONLY: True,
    CONF_DAILY_LIMIT: 75,
    CONF_CALIBRATION_LIMIT: 100,
    CONF_CALIBRATION_INTERVAL_DAYS: 14,
    CONF_MIN_HOLD_MINUTES: 10,
    CONF_TOP_BALANCING_TIMEOUT_MINUTES: 45,
    CONF_TOP_BALANCING_BUFFER_SECONDS: 30,
    CONF_FINISHED_STATUSES: ["complete", "stopped", "disconnected", "no_power"],
    CONF_ABORT_AFTER_HOURS: 12,
    CONF_RESTORE_AFTER_MANUAL: True,
    CONF_SEND_NOTIFICATIONS: True,
    CONF_NOTIFY_SERVICE: None,
}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading the integration from custom_components."""


@pytest.fixture(autouse=True)
async def set_value_calls(hass: HomeAssistant, request: pytest.FixtureRequest) -> list[ServiceCall]:
    """Capture number.set_value calls.

    The number component is set up first: otherwise it registers its own
    ``set_value`` when the integration's number platform loads and replaces the
    mock. Tests marked ``real_number`` keep the real service (to exercise the
    integration's own number entities).
    """
    assert await async_setup_component(hass, "number", {})
    if request.node.get_closest_marker("real_number"):
        return []
    return async_mock_service(hass, "number", "set_value")


@pytest.fixture(autouse=True)
async def notifications(hass: HomeAssistant) -> list[ServiceCall]:
    """Capture persistent notifications."""
    assert await async_setup_component(hass, "persistent_notification", {})
    return async_mock_service(hass, "persistent_notification", "create")


def set_vehicle(
    hass: HomeAssistant,
    *,
    soc: Any = 50,
    limit: Any = 75,
    status: str = "charging",
    cable: str | None = "on",
    location: str | None = "home",
) -> None:
    """Publish the states of the fake vehicle."""
    hass.states.async_set(BATTERY, soc)
    hass.states.async_set(LIMIT, limit)
    hass.states.async_set(STATUS, status)
    if cable is not None:
        hass.states.async_set(CABLE, cable)
    if location is not None:
        hass.states.async_set(LOCATION, location)


def make_entry(
    data: dict[str, Any] | None = None, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Create a config entry for the fake vehicle."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Car",
        unique_id=LIMIT,
        entry_id=ENTRY_ID,
        data={**BASE_DATA, **(data or {})},
        options=options or {},
    )


async def setup_entry(
    hass: HomeAssistant, entry: MockConfigEntry | None = None
) -> tuple[MockConfigEntry, EvLfpCoordinator]:
    """Add and set up an entry, returning it with its coordinator."""
    entry = entry or make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, entry.runtime_data


async def advance(hass: HomeAssistant, freezer: Any, **delta: float) -> None:
    """Move time forward and let timers/debouncers run."""
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def settle(hass: HomeAssistant, freezer: Any) -> None:
    """Let the state-change debouncer fire."""
    await advance(hass, freezer, seconds=3)


def failing_set_value(hass: HomeAssistant) -> list[ServiceCall]:
    """Replace number.set_value with a handler that always fails."""
    calls: list[ServiceCall] = []

    def _fail(call: ServiceCall) -> None:
        calls.append(call)
        raise HomeAssistantError("car asleep")

    hass.services.async_register("number", "set_value", _fail)
    return calls


@pytest.fixture
def stored(hass_storage: dict[str, Any]) -> Generator[Any]:
    """Helper to preload the coordinator storage."""

    def _preload(data: dict[str, Any]) -> None:
        hass_storage[STORAGE] = {
            "version": 1,
            "minor_version": 1,
            "key": STORAGE,
            "data": data,
        }

    yield _preload
