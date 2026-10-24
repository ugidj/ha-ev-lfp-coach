"""The EV LFP Battery Coach integration."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import EvLfpConfigEntry, EvLfpCoordinator, async_cleanup_entry
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration services (once, not per config entry)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EvLfpConfigEntry) -> bool:
    """Set up EV LFP Battery Coach from a config entry."""
    coordinator = EvLfpCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listeners last: decisions start only after Home Assistant has started
    # and the vehicle integration had the chance to create its entities.
    coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EvLfpConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: EvLfpConfigEntry) -> None:
    """Delete stored state and Repairs issues when the entry is removed."""
    await async_cleanup_entry(hass, entry.entry_id)
