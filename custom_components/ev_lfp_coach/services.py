"""Services of EV LFP Battery Coach."""

from __future__ import annotations

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service
import voluptuous as vol

from .const import (
    ATTR_TIMESTAMP,
    DOMAIN,
    SERVICE_CANCEL_CALIBRATION,
    SERVICE_MARK_CALIBRATED,
    SERVICE_START_CALIBRATION,
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the entity services, targeting the calibration state sensor."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_START_CALIBRATION,
        entity_domain=SENSOR_DOMAIN,
        schema=None,
        func="async_start_calibration",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_CANCEL_CALIBRATION,
        entity_domain=SENSOR_DOMAIN,
        schema=None,
        func="async_cancel_calibration",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_MARK_CALIBRATED,
        entity_domain=SENSOR_DOMAIN,
        schema={vol.Optional(ATTR_TIMESTAMP): cv.datetime},
        func="async_mark_calibrated",
    )
