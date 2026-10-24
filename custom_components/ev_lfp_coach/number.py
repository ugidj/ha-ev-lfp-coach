"""Number entities to tune the coach from a dashboard."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CALIBRATION_INTERVAL_DAYS,
    CONF_CALIBRATION_LIMIT,
    CONF_DAILY_LIMIT,
)
from .coordinator import CoachData, EvLfpConfigEntry, EvLfpCoordinator
from .entity import EvLfpEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class EvLfpNumberDescription(NumberEntityDescription):
    """Describe a coach parameter."""

    option_key: str
    value_fn: Callable[[CoachData], int]


NUMBERS: tuple[EvLfpNumberDescription, ...] = (
    EvLfpNumberDescription(
        key="daily_limit",
        option_key=CONF_DAILY_LIMIT,
        entity_category=EntityCategory.CONFIG,
        native_min_value=50,
        native_max_value=90,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        value_fn=lambda data: data.daily_limit,
    ),
    EvLfpNumberDescription(
        key="calibration_limit",
        option_key=CONF_CALIBRATION_LIMIT,
        entity_category=EntityCategory.CONFIG,
        native_min_value=90,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        value_fn=lambda data: data.calibration_limit,
    ),
    EvLfpNumberDescription(
        key="calibration_interval_days",
        option_key=CONF_CALIBRATION_INTERVAL_DAYS,
        entity_category=EntityCategory.CONFIG,
        native_min_value=1,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.DAYS,
        mode=NumberMode.BOX,
        value_fn=lambda data: data.calibration_interval_days,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvLfpConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the coach number entities."""
    coordinator = entry.runtime_data
    async_add_entities(EvLfpNumber(coordinator, description) for description in NUMBERS)


class EvLfpNumber(EvLfpEntity, NumberEntity):
    """A coach parameter stored in the config entry options."""

    entity_description: EvLfpNumberDescription

    def __init__(self, coordinator: EvLfpCoordinator, description: EvLfpNumberDescription) -> None:
        """Initialize the number."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int:
        """Return the current parameter value."""
        return self.entity_description.value_fn(self.coach)

    async def async_set_native_value(self, value: float) -> None:
        """Persist a new parameter value."""
        await self.coordinator.async_set_parameter(self.entity_description.option_key, int(value))
