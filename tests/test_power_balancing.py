"""Top balancing decided by the power the car draws."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.ev_lfp_coach.const import (
    CONF_CHARGING_POWER_ENTITY,
    EVENT_CALIBRATION_COMPLETED,
    STATE_CALIBRATED,
    STATE_TOP_BALANCING,
)

from .conftest import (
    POWER,
    STATUS,
    advance,
    make_entry,
    set_vehicle,
    settle,
    setup_entry,
)


def _values(calls: list[ServiceCall]) -> list[int]:
    return [call.data["value"] for call in calls]


def _power(hass: HomeAssistant, value: Any, unit: str = "kW") -> None:
    hass.states.async_set(POWER, value, {"unit_of_measurement": unit})


async def _top_with_power(hass: HomeAssistant, freezer: Any, *, kw: float = 1.2) -> Any:
    """Calibrating coach with a power sensor, battery at the top, car drawing ``kw``."""
    _power(hass, 7.0)
    set_vehicle(hass, soc=80, limit=75)
    _, coordinator = await setup_entry(hass, make_entry(data={CONF_CHARGING_POWER_ENTITY: POWER}))
    set_vehicle(hass, soc=100, limit=100, status="charging")
    _power(hass, kw)
    await settle(hass, freezer)
    assert coordinator.state == STATE_TOP_BALANCING
    return coordinator


async def test_complete_status_is_ignored_while_the_car_still_draws_power(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """The Tesla trap: "complete" at 100% while the BMS is still balancing."""
    coordinator = await _top_with_power(hass, freezer)

    hass.states.async_set(STATUS, "complete")
    await advance(hass, freezer, minutes=30)

    assert coordinator.state == STATE_TOP_BALANCING
    assert _values(set_value_calls) == [100]


async def test_balancing_ends_when_the_car_stops_drawing_current(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """Idle power for the configured time, after the minimum hold, ends it."""
    done = async_capture_events(hass, EVENT_CALIBRATION_COMPLETED)
    coordinator = await _top_with_power(hass, freezer, kw=0.4)
    await advance(hass, freezer, minutes=11)  # past the 10 min minimum hold
    assert coordinator.state == STATE_TOP_BALANCING

    _power(hass, 0.0)
    await settle(hass, freezer)
    await advance(hass, freezer, minutes=2)
    assert coordinator.state == STATE_TOP_BALANCING  # needs 3 idle minutes
    await advance(hass, freezer, minutes=2)
    assert coordinator.state == STATE_TOP_BALANCING  # then the 30 s buffer
    await advance(hass, freezer, seconds=31)

    assert coordinator.state == STATE_CALIBRATED
    assert _values(set_value_calls) == [100, 75]
    assert done[0].data["reason"] == "power"


async def test_a_power_blip_restarts_the_idle_countdown(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """The car pausing for a moment between balancing bursts is not the end."""
    coordinator = await _top_with_power(hass, freezer)
    await advance(hass, freezer, minutes=11)

    _power(hass, 0.0)
    await advance(hass, freezer, minutes=2)
    _power(hass, 0.3)
    await advance(hass, freezer, seconds=20)
    _power(hass, 0.0)
    await advance(hass, freezer, minutes=2)

    assert coordinator.state == STATE_TOP_BALANCING
    assert _values(set_value_calls) == [100]


async def test_unknown_power_never_ends_balancing(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """A wall box that drops offline is not "zero current"."""
    coordinator = await _top_with_power(hass, freezer)
    await advance(hass, freezer, minutes=11)

    hass.states.async_set(POWER, "unavailable")
    await advance(hass, freezer, minutes=20)

    assert coordinator.state == STATE_TOP_BALANCING
    assert _values(set_value_calls) == [100]


async def test_watts_and_kilowatts_are_both_understood(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """40 W is idle (threshold 50 W); 0.04 kW is the same thing."""
    coordinator = await _top_with_power(hass, freezer)
    await advance(hass, freezer, minutes=11)

    _power(hass, 400, "W")  # still drawing
    await advance(hass, freezer, minutes=10)
    assert coordinator.state == STATE_TOP_BALANCING

    _power(hass, 0.04, "kW")
    await settle(hass, freezer)
    await advance(hass, freezer, minutes=4)
    await advance(hass, freezer, seconds=31)

    assert coordinator.state == STATE_CALIBRATED


async def test_safety_timeout_still_applies_with_a_power_sensor(
    hass: HomeAssistant, freezer: Any, set_value_calls: list[ServiceCall]
) -> None:
    """Even a car that keeps drawing current gets its daily limit back."""
    coordinator = await _top_with_power(hass, freezer)

    await advance(hass, freezer, minutes=46)
    await advance(hass, freezer, seconds=31)

    assert coordinator.state == STATE_CALIBRATED
    assert _values(set_value_calls) == [100, 75]
