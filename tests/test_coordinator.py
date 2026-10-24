"""Tests for the calibration state machine."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, ServiceCall
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_mock_service,
)

from custom_components.ev_lfp_coach.const import (
    CONF_CALIBRATION_LIMIT,
    CONF_HOME_ONLY,
    CONF_LOCATION_ENTITY,
    CONF_NOTIFY_SERVICE,
    CONF_RESTORE_AFTER_MANUAL,
    DOMAIN,
    EVENT_BALANCING_STARTED,
    EVENT_CALIBRATION_ABORTED,
    EVENT_CALIBRATION_COMPLETED,
    EVENT_CALIBRATION_STARTED,
    ISSUE_ENTITY_MISSING,
    ISSUE_SET_LIMIT_FAILED,
    STATE_CALIBRATED,
    STATE_CALIBRATING_100,
    STATE_CALIBRATION_DUE,
    STATE_TOP_BALANCING,
)

from .conftest import (
    BATTERY,
    CABLE,
    ENTRY_ID,
    LIMIT,
    STATUS,
    advance,
    failing_set_value,
    make_entry,
    set_vehicle,
    settle,
    setup_entry,
)


def _values(calls: list[ServiceCall]) -> list[int]:
    return [call.data["value"] for call in calls]


def _recent(days: float = 1) -> str:
    return (dt_util.utcnow() - timedelta(days=days)).isoformat()


async def _start_calibrating(hass: HomeAssistant) -> Any:
    """Set up a due vehicle so that the coach starts calibrating."""
    set_vehicle(hass, soc=80, limit=75)
    return await setup_entry(hass)


async def _reach_top(hass: HomeAssistant, freezer: Any) -> None:
    set_vehicle(hass, soc=100, limit=100, status="charging")
    await settle(hass, freezer)


# -----------------------------------------------------------------------------
# Robustness against bad data
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kwargs",
    [
        {"soc": "unavailable"},
        {"soc": "unknown"},
        {"limit": "unavailable"},
        {"limit": "unknown"},
        {"soc": "abc"},
    ],
)
async def test_untrustworthy_core_data_never_triggers_actions(
    hass: HomeAssistant, set_value_calls: list[ServiceCall], notifications: list, kwargs: dict
) -> None:
    """unavailable/unknown/garbage must not be read as 0 and cause commands."""
    set_vehicle(hass, **kwargs)
    _, coordinator = await setup_entry(hass)

    assert not set_value_calls
    assert not notifications
    assert coordinator.state == STATE_CALIBRATION_DUE


@pytest.mark.parametrize(
    "kwargs",
    [
        {"location": "not_home"},
        {"location": "unavailable"},
        {"cable": "off"},
        {"cable": "unavailable"},
        {"cable": "unknown"},
    ],
)
async def test_does_not_start_unless_home_and_plugged(
    hass: HomeAssistant, set_value_calls: list[ServiceCall], kwargs: dict
) -> None:
    """Starting needs positive evidence: unknown is not "home and plugged"."""
    set_vehicle(hass, soc=60, limit=75, **kwargs)
    _, coordinator = await setup_entry(hass)

    assert not set_value_calls
    assert coordinator.state == STATE_CALIBRATION_DUE


async def test_calibration_can_start_away_from_home_when_not_restricted(
    hass: HomeAssistant, set_value_calls: list[ServiceCall]
) -> None:
    """Without 'only at home' the location is ignored entirely."""
    set_vehicle(hass, soc=60, limit=75, location="not_home")
    entry = make_entry(data={CONF_HOME_ONLY: False})

    await setup_entry(hass, entry)

    assert _values(set_value_calls) == [100]


async def test_location_is_ignored_without_a_tracker_even_if_home_only(
    hass: HomeAssistant, set_value_calls: list[ServiceCall]
) -> None:
    """'Only at home' with no tracker configured cannot block anything."""
    set_vehicle(hass, soc=60, limit=75)
    entry = make_entry(data={CONF_HOME_ONLY: True, CONF_LOCATION_ENTITY: None})

    _, coordinator = await setup_entry(hass, entry)

    assert not coordinator.home_only
    assert _values(set_value_calls) == [100]


async def test_decimal_comma_and_float_states_are_parsed(
    hass: HomeAssistant, set_value_calls: list[ServiceCall]
) -> None:
    """Numeric parsing accepts "75.0" and "75,0"."""
    set_vehicle(hass, soc="60,5", limit="75.0")
    await setup_entry(hass)

    assert _values(set_value_calls) == [100]


async def test_no_decisions_before_home_assistant_started(
    hass: HomeAssistant, set_value_calls: list[ServiceCall]
) -> None:
    """Vehicle entities may not exist yet while Home Assistant boots."""
    hass.set_state(CoreState.starting)
    set_vehicle(hass, soc=60, limit=75)
    _, coordinator = await setup_entry(hass)
    assert not set_value_calls

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert _values(set_value_calls) == [100]
    assert coordinator.state == STATE_CALIBRATING_100


# -----------------------------------------------------------------------------
# Starting a calibration
# -----------------------------------------------------------------------------
async def test_starts_calibration_when_due_home_and_plugged(
    hass: HomeAssistant, set_value_calls: list[ServiceCall], notifications: list
) -> None:
    """A due calibration raises the limit and announces it once."""
    events = async_capture_events(hass, EVENT_CALIBRATION_STARTED)
    _, coordinator = await _start_calibrating(hass)

    assert _values(set_value_calls) == [100]
    assert set_value_calls[0].data["entity_id"] == LIMIT
    assert coordinator.state == STATE_CALIBRATING_100
    assert coordinator.started_by_coach
    assert len(events) == 1
    assert len(notifications) == 1


async def test_duplicate_triggers_send_a_single_command(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """Bursts of updates while the cloud lags must not repeat the command."""
    _, coordinator = await _start_calibrating(hass)

    for soc in (81, 82, 83, 84):
        hass.states.async_set(BATTERY, soc)
    await settle(hass, freezer)
    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert _values(set_value_calls) == [100]


async def test_command_is_repeated_if_limit_never_changes(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """If the vehicle still reports the old limit after the grace, re-assert it."""
    await _start_calibrating(hass)

    await advance(hass, freezer, minutes=6)

    assert _values(set_value_calls) == [100, 100]


async def test_not_due_means_no_command(
    hass: HomeAssistant, set_value_calls: list[ServiceCall], stored: Any
) -> None:
    """A recent calibration leaves the car alone."""
    stored({"last_100": _recent(2)})
    set_vehicle(hass, soc=60, limit=75)
    _, coordinator = await setup_entry(hass)

    assert not set_value_calls
    assert coordinator.state == STATE_CALIBRATED


async def test_interval_counts_calendar_days(hass: HomeAssistant, freezer: Any) -> None:
    """Due from local midnight of day N, so evening calibrations do not drift."""
    set_vehicle(hass)
    _, coordinator = await setup_entry(hass)
    local = dt_util.DEFAULT_TIME_ZONE
    coordinator.last_100 = datetime(2026, 10, 1, 22, 30, tzinfo=local)

    midnight = datetime(2026, 10, 15, 0, 0, tzinfo=local)
    assert coordinator.next_calibration == midnight

    freezer.move_to(midnight - timedelta(minutes=1))
    assert not coordinator.is_calibration_due
    freezer.move_to(midnight + timedelta(minutes=1))
    assert coordinator.is_calibration_due


# -----------------------------------------------------------------------------
# Top balancing
# -----------------------------------------------------------------------------
async def test_full_cycle_holds_for_min_time_then_restores_daily_limit(
    hass: HomeAssistant,
    freezer: Any,
    set_value_calls: list[ServiceCall],
    notifications: list,
) -> None:
    """The limit is untouched while balancing, even if the car says "complete"."""
    balancing = async_capture_events(hass, EVENT_BALANCING_STARTED)
    done = async_capture_events(hass, EVENT_CALIBRATION_COMPLETED)
    _, coordinator = await _start_calibrating(hass)

    await _reach_top(hass, freezer)
    assert coordinator.state == STATE_TOP_BALANCING
    assert coordinator.last_100 is not None
    assert len(balancing) == 1

    # The car reports "complete" at once: min hold (10 min) is not over.
    hass.states.async_set(STATUS, "complete")
    await advance(hass, freezer, minutes=5)
    assert coordinator.state == STATE_TOP_BALANCING
    assert _values(set_value_calls) == [100]

    # Hold over: finish is scheduled, then the buffer (30 s) must pass.
    await advance(hass, freezer, minutes=6)
    assert coordinator.state == STATE_TOP_BALANCING
    await advance(hass, freezer, seconds=31)

    assert coordinator.state == STATE_CALIBRATED
    assert _values(set_value_calls) == [100, 75]
    assert len(done) == 1
    assert done[0].data["reason"] == "status"
    assert len(coordinator.history) == 1
    assert len(notifications) == 3  # started, balancing, completed


async def test_status_flipping_back_cancels_the_pending_finish(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """If charging resumes during the buffer, keep balancing."""
    _, coordinator = await _start_calibrating(hass)
    await _reach_top(hass, freezer)
    await advance(hass, freezer, minutes=11)

    hass.states.async_set(STATUS, "complete")
    await settle(hass, freezer)
    assert coordinator._finish_at is not None
    hass.states.async_set(STATUS, "charging")
    await advance(hass, freezer, seconds=40)

    assert coordinator.state == STATE_TOP_BALANCING
    assert _values(set_value_calls) == [100]


async def test_timeout_restores_limit_even_if_car_keeps_charging(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """Safety net: the daily limit comes back after the timeout."""
    done = async_capture_events(hass, EVENT_CALIBRATION_COMPLETED)
    _, coordinator = await _start_calibrating(hass)
    await _reach_top(hass, freezer)

    await advance(hass, freezer, minutes=44)
    assert coordinator.state == STATE_TOP_BALANCING
    await advance(hass, freezer, minutes=2)
    await advance(hass, freezer, seconds=31)

    assert coordinator.state == STATE_CALIBRATED
    assert _values(set_value_calls) == [100, 75]
    assert done[0].data["reason"] == "timeout"


async def test_unavailable_cable_does_not_end_balancing(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """A sleeping/offline car must not look like "unplugged"."""
    _, coordinator = await _start_calibrating(hass)
    await _reach_top(hass, freezer)

    hass.states.async_set(CABLE, "unavailable")
    await settle(hass, freezer)
    await advance(hass, freezer, minutes=5)

    assert coordinator.state == STATE_TOP_BALANCING
    assert _values(set_value_calls) == [100]


async def test_unplugging_ends_balancing_immediately(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """With the cable out there is nothing left to wait for."""
    done = async_capture_events(hass, EVENT_CALIBRATION_COMPLETED)
    _, coordinator = await _start_calibrating(hass)
    await _reach_top(hass, freezer)

    hass.states.async_set(CABLE, "off")
    await settle(hass, freezer)

    assert coordinator.state == STATE_CALIBRATED
    assert _values(set_value_calls) == [100, 75]
    assert done[0].data["reason"] == "unplugged"


async def test_bursts_of_events_while_finishing_do_not_duplicate(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall], notifications: list
) -> None:
    """Many updates during the buffer still produce one restore and one notification."""
    await _start_calibrating(hass)
    await _reach_top(hass, freezer)
    await advance(hass, freezer, minutes=11)
    hass.states.async_set(STATUS, "complete")
    await settle(hass, freezer)

    for step in range(5):
        hass.states.async_set(BATTERY, 99 - step % 2)
        await advance(hass, freezer, seconds=7)

    assert _values(set_value_calls) == [100, 75]
    assert len(notifications) == 3


async def test_lower_calibration_limit_reaches_its_own_target(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """With a 95% calibration limit the cycle must not wait for 99%."""
    entry = make_entry(data={CONF_CALIBRATION_LIMIT: 95})
    set_vehicle(hass, soc=80, limit=75)
    _, coordinator = await setup_entry(hass, entry)
    assert _values(set_value_calls) == [95]

    set_vehicle(hass, soc=95, limit=95)
    await settle(hass, freezer)

    assert coordinator.state == STATE_TOP_BALANCING


# -----------------------------------------------------------------------------
# Restart resilience
# -----------------------------------------------------------------------------
async def test_restart_during_balancing_resumes_with_remaining_time(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall], stored: Any
) -> None:
    """Persisted phase + timestamp: the timeout keeps counting across restarts."""
    stored(
        {
            "last_100": _recent(0.01),
            "phase": STATE_TOP_BALANCING,
            "phase_started": (dt_util.utcnow() - timedelta(minutes=30)).isoformat(),
            "started_by_coach": True,
        }
    )
    set_vehicle(hass, soc=100, limit=100, status="charging")
    _, coordinator = await setup_entry(hass)

    assert coordinator.state == STATE_TOP_BALANCING
    assert not set_value_calls

    await advance(hass, freezer, minutes=16)
    await advance(hass, freezer, seconds=31)

    assert coordinator.state == STATE_CALIBRATED
    assert _values(set_value_calls) == [75]


async def test_restart_with_expired_balancing_finishes_instead_of_sticking(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall], stored: Any
) -> None:
    """The limit must never stay at 100% because of a lost timer."""
    stored(
        {
            "last_100": _recent(0.1),
            "phase": STATE_TOP_BALANCING,
            "phase_started": (dt_util.utcnow() - timedelta(hours=3)).isoformat(),
            "started_by_coach": True,
        }
    )
    set_vehicle(hass, soc=97, limit=100, status="charging")
    _, coordinator = await setup_entry(hass)
    await advance(hass, freezer, seconds=31)

    assert coordinator.state == STATE_CALIBRATED
    assert _values(set_value_calls) == [75]


async def test_restart_while_calibrating_resumes_the_cycle(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall], stored: Any
) -> None:
    """A calibration interrupted by a restart continues towards the top."""
    stored(
        {
            "phase": STATE_CALIBRATING_100,
            "phase_started": _recent(0.01),
            "started_by_coach": True,
        }
    )
    set_vehicle(hass, soc=90, limit=100)
    _, coordinator = await setup_entry(hass)
    assert coordinator.state == STATE_CALIBRATING_100
    assert not set_value_calls

    set_vehicle(hass, soc=100, limit=100)
    await settle(hass, freezer)
    assert coordinator.state == STATE_TOP_BALANCING


async def test_garbage_in_storage_is_ignored(hass: HomeAssistant, stored: Any) -> None:
    """Corrupt persisted values must not break the setup."""
    stored(
        {
            "last_100": "not-a-date",
            "phase": "nonsense",
            "history": "oops",
            "force_next": None,
        }
    )
    set_vehicle(hass, cable="off")
    _, coordinator = await setup_entry(hass)

    assert coordinator.last_100 is None
    assert coordinator.phase is None
    assert coordinator.history == []


# -----------------------------------------------------------------------------
# Aborting
# -----------------------------------------------------------------------------
async def test_calibration_is_abandoned_after_being_away_too_long(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """Unplugged for 12h while calibrating: put the daily limit back."""
    aborted = async_capture_events(hass, EVENT_CALIBRATION_ABORTED)
    _, coordinator = await _start_calibrating(hass)

    hass.states.async_set(CABLE, "off")
    await settle(hass, freezer)
    assert coordinator.state == STATE_CALIBRATING_100
    await advance(hass, freezer, hours=11)
    assert coordinator.state == STATE_CALIBRATING_100
    await advance(hass, freezer, hours=2)

    assert coordinator.state == STATE_CALIBRATION_DUE
    assert _values(set_value_calls) == [100, 75]
    assert aborted[0].data["reason"] == "unplugged"


async def test_replugging_cancels_the_away_countdown(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """Coming back before the deadline keeps the calibration alive."""
    _, coordinator = await _start_calibrating(hass)
    hass.states.async_set(CABLE, "off")
    await settle(hass, freezer)
    await advance(hass, freezer, hours=5)
    hass.states.async_set(CABLE, "on")
    await settle(hass, freezer)
    assert coordinator.unplugged_since is None

    await advance(hass, freezer, hours=10)

    assert coordinator.state == STATE_CALIBRATING_100
    assert 75 not in _values(set_value_calls)


async def test_cancel_stops_the_cycle_and_does_not_restart(
    hass: HomeAssistant, set_value_calls: list[ServiceCall]
) -> None:
    """Cancelling restores the daily limit and snoozes the request."""
    _, coordinator = await _start_calibrating(hass)

    await coordinator.async_cancel_calibration()
    assert _values(set_value_calls) == [100, 75]
    assert coordinator.state == STATE_CALIBRATION_DUE

    set_vehicle(hass, soc=80, limit=75)
    await coordinator.async_refresh()
    assert _values(set_value_calls) == [100, 75]


# -----------------------------------------------------------------------------
# Manual full charges, buttons and switch
# -----------------------------------------------------------------------------
async def test_manual_full_charge_counts_and_restores_limit(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall], stored: Any
) -> None:
    """A 100% charge the user started resets the clock and is cleaned up."""
    stored({"last_100": _recent(20)})
    set_vehicle(hass, soc=100, limit=100)
    _, coordinator = await setup_entry(hass)

    assert coordinator.state == STATE_TOP_BALANCING
    assert not coordinator.started_by_coach
    assert coordinator.days_since_last_calibration == 0.0

    await advance(hass, freezer, minutes=46)
    await advance(hass, freezer, seconds=31)
    assert coordinator.state == STATE_CALIBRATED
    assert _values(set_value_calls) == [75]


async def test_manual_full_charge_can_be_left_alone(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall], stored: Any
) -> None:
    """With restore_after_manual off the user's 100% limit is never touched."""
    stored({"last_100": _recent(20)})
    set_vehicle(hass, soc=100, limit=100)
    entry = make_entry(data={CONF_RESTORE_AFTER_MANUAL: False})
    _, coordinator = await setup_entry(hass, entry)

    await advance(hass, freezer, minutes=46)
    await advance(hass, freezer, seconds=31)
    first_calibration = coordinator.last_100
    await advance(hass, freezer, hours=3)

    assert coordinator.state == STATE_CALIBRATED
    assert not set_value_calls
    assert coordinator.last_100 == first_calibration  # not re-counted forever


async def test_force_calibration_overrides_the_interval(
    hass: HomeAssistant, set_value_calls: list[ServiceCall], stored: Any
) -> None:
    """The button starts a calibration even if the last one was yesterday."""
    stored({"last_100": _recent(1)})
    set_vehicle(hass, soc=70, limit=75)
    _, coordinator = await setup_entry(hass)
    assert not set_value_calls

    await coordinator.async_force_calibration()

    assert _values(set_value_calls) == [100]
    assert coordinator.state == STATE_CALIBRATING_100


async def test_mark_calibrated_ends_a_running_cycle(
    hass: HomeAssistant, set_value_calls: list[ServiceCall]
) -> None:
    """Marking the battery calibrated while charging to 100 restores the limit."""
    _, coordinator = await _start_calibrating(hass)

    await coordinator.async_mark_calibrated()

    assert coordinator.state == STATE_CALIBRATED
    assert _values(set_value_calls) == [100, 75]
    assert coordinator.phase is None


async def test_mark_calibrated_accepts_a_past_timestamp(hass: HomeAssistant) -> None:
    """A full charge done a week ago can be recorded."""
    set_vehicle(hass, soc=60, limit=75, cable="off")
    _, coordinator = await setup_entry(hass)

    await coordinator.async_mark_calibrated(dt_util.utcnow() - timedelta(days=7))

    assert coordinator.days_since_last_calibration == 7.0
    assert coordinator.state == STATE_CALIBRATED


async def test_disabling_restores_limit_and_stops_decisions(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """Pausing the coach mid-calibration must not leave the car at 100%."""
    _, coordinator = await _start_calibrating(hass)

    await coordinator.async_set_enabled(False)
    assert _values(set_value_calls) == [100, 75]

    set_vehicle(hass, soc=100, limit=100)
    await settle(hass, freezer)
    assert coordinator.phase is None
    assert _values(set_value_calls) == [100, 75]


async def test_options_override_data_and_none_clears_a_value(hass: HomeAssistant) -> None:
    """A None option (cleared field) wins over the value saved at setup."""
    entry = make_entry(
        data={CONF_NOTIFY_SERVICE: "notify.old"},
        options={CONF_NOTIFY_SERVICE: None},
    )
    set_vehicle(hass)
    _, coordinator = await setup_entry(hass, entry)

    assert coordinator.notify_service is None


# -----------------------------------------------------------------------------
# Command failures
# -----------------------------------------------------------------------------
async def test_failing_commands_back_off_raise_an_issue_and_recover(
    hass: HomeAssistant, freezer: Any, notifications: list
) -> None:
    """A car that will not answer: retry slowly, tell the user, recover quietly."""
    calls = failing_set_value(hass)
    _, coordinator = await _start_calibrating(hass)
    assert len(calls) == 1
    assert coordinator.state == STATE_CALIBRATION_DUE

    await settle(hass, freezer)
    assert len(calls) == 1  # back-off: no hammering

    await advance(hass, freezer, minutes=5, seconds=5)
    await advance(hass, freezer, minutes=5, seconds=5)
    assert len(calls) == 3
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"{ISSUE_SET_LIMIT_FAILED}_{ENTRY_ID}")
    assert len(notifications) == 1

    ok = async_mock_service(hass, "number", "set_value")
    await advance(hass, freezer, minutes=5, seconds=5)

    assert len(ok) == 1
    assert coordinator.state == STATE_CALIBRATING_100
    assert registry.async_get_issue(DOMAIN, f"{ISSUE_SET_LIMIT_FAILED}_{ENTRY_ID}") is None


async def test_failed_restore_keeps_the_phase_and_retries(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """If the daily limit cannot be restored, the cycle stays open until it can."""
    _, coordinator = await _start_calibrating(hass)
    await _reach_top(hass, freezer)
    calls = failing_set_value(hass)

    hass.states.async_set(CABLE, "off")
    await settle(hass, freezer)
    assert coordinator.state == STATE_TOP_BALANCING
    assert len(calls) == 1

    ok = async_mock_service(hass, "number", "set_value")
    await advance(hass, freezer, minutes=5, seconds=5)

    assert coordinator.state == STATE_CALIBRATED
    assert _values(ok) == [75]


async def test_missing_entity_raises_and_clears_a_repair_issue(hass: HomeAssistant) -> None:
    """Configured entities that vanish are reported in Repairs."""
    set_vehicle(hass)
    hass.states.async_remove(STATUS)
    _, coordinator = await setup_entry(hass)
    registry = ir.async_get(hass)
    issue_id = f"{ISSUE_ENTITY_MISSING}_{ENTRY_ID}_charging_status_entity"
    assert registry.async_get_issue(DOMAIN, issue_id)

    hass.states.async_set(STATUS, "charging")
    await coordinator.async_refresh()

    assert registry.async_get_issue(DOMAIN, issue_id) is None


# -----------------------------------------------------------------------------
# Cable derived from status when no cable sensor is configured
# -----------------------------------------------------------------------------
async def test_without_cable_sensor_status_disconnected_means_unplugged(
    hass: HomeAssistant, set_value_calls: list[ServiceCall]
) -> None:
    """No cable entity: "disconnected" blocks the start, any other status allows it."""
    data = {"cable_entity": None, "location_entity": None}
    set_vehicle(hass, soc=60, limit=75, status="disconnected", cable=None, location=None)
    _, coordinator = await setup_entry(hass, make_entry(data=data))
    assert not set_value_calls

    hass.states.async_set(STATUS, "stopped")
    await coordinator.async_refresh()

    assert _values(set_value_calls) == [100]
