"""Buttons for EV LFP Battery Coach."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EvLfpConfigEntry, EvLfpCoordinator
from .entity import EvLfpEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class EvLfpButtonDescription(ButtonEntityDescription):
    """Describe a coach button."""

    press_fn: Callable[[EvLfpCoordinator], Awaitable[None]]


BUTTONS: tuple[EvLfpButtonDescription, ...] = (
    EvLfpButtonDescription(
        key="force_calibration",
        press_fn=lambda coordinator: coordinator.async_force_calibration(),
    ),
    EvLfpButtonDescription(
        key="cancel_calibration",
        press_fn=lambda coordinator: coordinator.async_cancel_calibration(),
    ),
    EvLfpButtonDescription(
        key="reset_calibration_timer",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coordinator: coordinator.async_mark_calibrated(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvLfpConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the coach buttons."""
    coordinator = entry.runtime_data
    async_add_entities(EvLfpButton(coordinator, description) for description in BUTTONS)


class EvLfpButton(EvLfpEntity, ButtonEntity):
    """A button that triggers a coordinator action."""

    entity_description: EvLfpButtonDescription

    def __init__(self, coordinator: EvLfpCoordinator, description: EvLfpButtonDescription) -> None:
        """Initialize the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.entity_description.press_fn(self.coordinator)
