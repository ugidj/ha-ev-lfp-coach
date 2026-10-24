"""Binary sensors for EV LFP Battery Coach."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import CoachData, EvLfpConfigEntry, EvLfpCoordinator
from .entity import EvLfpEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EvLfpBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a coach binary sensor."""

    value_fn: Callable[[CoachData], bool]


BINARY_SENSORS: tuple[EvLfpBinarySensorDescription, ...] = (
    EvLfpBinarySensorDescription(
        key="calibration_due",
        value_fn=lambda data: data.calibration_due,
    ),
    EvLfpBinarySensorDescription(
        key="calibration_active",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda data: data.active,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvLfpConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the coach binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        EvLfpBinarySensor(coordinator, description) for description in BINARY_SENSORS
    )


class EvLfpBinarySensor(EvLfpEntity, BinarySensorEntity):
    """A binary sensor read from the coordinator snapshot."""

    entity_description: EvLfpBinarySensorDescription

    def __init__(
        self, coordinator: EvLfpCoordinator, description: EvLfpBinarySensorDescription
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return the binary state."""
        return self.entity_description.value_fn(self.coach)
