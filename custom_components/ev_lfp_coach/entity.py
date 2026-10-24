"""Base entity for EV LFP Battery Coach."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CoachData, EvLfpCoordinator


class EvLfpEntity(CoordinatorEntity[EvLfpCoordinator]):
    """Common behaviour of all coach entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EvLfpCoordinator, key: str) -> None:
        """Initialize the entity; ``key`` is the translation key and unique id suffix."""
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=coordinator.vehicle_name,
            manufacturer="EV LFP Coach",
            model="LFP battery manager",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def coach(self) -> CoachData:
        """Return the latest coordinator snapshot."""
        return self.coordinator.data
