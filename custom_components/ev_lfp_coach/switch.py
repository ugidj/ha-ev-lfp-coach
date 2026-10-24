"""Switch to enable or disable the automatic management."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EvLfpConfigEntry, EvLfpCoordinator
from .entity import EvLfpEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvLfpConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the coach switch."""
    async_add_entities([AutoModeSwitch(entry.runtime_data)])


class AutoModeSwitch(EvLfpEntity, SwitchEntity):
    """Pause the coach without removing the integration."""

    def __init__(self, coordinator: EvLfpCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, "auto_mode")

    @property
    def is_on(self) -> bool:
        """Return True while the coach manages the vehicle."""
        return self.coach.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the automatic management."""
        await self.coordinator.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable it, restoring the daily limit if a calibration was running."""
        await self.coordinator.async_set_enabled(False)
