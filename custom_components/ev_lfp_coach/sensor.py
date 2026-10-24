"""Sensors for EV LFP Battery Coach."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CALIBRATION_STATES
from .coordinator import CoachData, EvLfpConfigEntry, EvLfpCoordinator
from .entity import EvLfpEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EvLfpSensorDescription(SensorEntityDescription):
    """Describe a coach sensor driven by a snapshot accessor."""

    value_fn: Callable[[CoachData], float | datetime | None]


SENSORS: tuple[EvLfpSensorDescription, ...] = (
    EvLfpSensorDescription(
        key="days_since_calibration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.days_since_calibration,
    ),
    EvLfpSensorDescription(
        key="last_calibration",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.last_calibration,
    ),
    EvLfpSensorDescription(
        key="next_calibration",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.next_calibration,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvLfpConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the coach sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            CalibrationStateSensor(coordinator),
            *(EvLfpSensor(coordinator, description) for description in SENSORS),
        ]
    )


class EvLfpSensor(EvLfpEntity, SensorEntity):
    """A sensor whose value is read from the coordinator snapshot."""

    entity_description: EvLfpSensorDescription

    def __init__(self, coordinator: EvLfpCoordinator, description: EvLfpSensorDescription) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | datetime | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coach)


class CalibrationStateSensor(EvLfpEntity, SensorEntity):
    """Workflow state of the calibration; target of the integration services."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CALIBRATION_STATES

    def __init__(self, coordinator: EvLfpCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "calibration_state")

    @property
    def native_value(self) -> str:
        """Return the workflow state."""
        return self.coach.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details about the current cycle and the last ones."""
        data = self.coach
        return {
            "phase_started": data.phase_started,
            "calibration_limit": data.calibration_limit,
            "daily_limit": data.daily_limit,
            "history": list(data.history),
        }

    # Entity service handlers (see services.py)
    async def async_start_calibration(self) -> None:
        """Calibrate at the next charge."""
        await self.coordinator.async_force_calibration()

    async def async_cancel_calibration(self) -> None:
        """Abort the running calibration."""
        await self.coordinator.async_cancel_calibration()

    async def async_mark_calibrated(self, timestamp: datetime | None = None) -> None:
        """Record a full charge."""
        await self.coordinator.async_mark_calibrated(timestamp)
