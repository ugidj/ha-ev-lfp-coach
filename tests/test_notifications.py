"""Tests for notification routing and localization."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.ev_lfp_coach.const import (
    CONF_NOTIFY_SERVICE,
    CONF_SEND_NOTIFICATIONS,
)
from custom_components.ev_lfp_coach.messages import fragment, render

from .conftest import make_entry, set_vehicle, setup_entry


async def test_legacy_notify_service_gets_title_message_and_mobile_data(
    hass: HomeAssistant,
) -> None:
    """notify.mobile_app_* receives a tag/channel to group the notifications."""
    calls = async_mock_service(hass, "notify", "mobile_app_phone")
    set_vehicle(hass, soc=80, limit=75)
    await setup_entry(hass, make_entry(data={CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"}))

    assert len(calls) == 1
    assert "Test Car" in calls[0].data["title"]
    assert calls[0].data["data"]["channel"] == "EV LFP Coach"


async def test_notify_entity_uses_send_message(hass: HomeAssistant) -> None:
    """notify.<entity> targets go through notify.send_message."""
    calls = async_mock_service(hass, "notify", "send_message")
    hass.states.async_set("notify.my_phone", "unknown")
    set_vehicle(hass, soc=80, limit=75)
    await setup_entry(hass, make_entry(data={CONF_NOTIFY_SERVICE: "notify.my_phone"}))

    assert len(calls) == 1
    assert calls[0].data["entity_id"] == "notify.my_phone"


async def test_missing_target_falls_back_to_a_persistent_notification(
    hass: HomeAssistant, notifications: list[ServiceCall]
) -> None:
    """A notify service that disappeared must not lose the message."""
    set_vehicle(hass, soc=80, limit=75)
    await setup_entry(hass, make_entry(data={CONF_NOTIFY_SERVICE: "notify.gone"}))

    assert len(notifications) == 1


async def test_notifications_can_be_disabled(
    hass: HomeAssistant, notifications: list[ServiceCall], set_value_calls: list[ServiceCall]
) -> None:
    """Disabled notifications still allow the workflow to run."""
    set_vehicle(hass, soc=80, limit=75)
    await setup_entry(hass, make_entry(data={CONF_SEND_NOTIFICATIONS: False}))

    assert not notifications
    assert len(set_value_calls) == 1


async def test_failing_notify_does_not_break_the_workflow(
    hass: HomeAssistant, set_value_calls: list[ServiceCall]
) -> None:
    """A broken notify service must never stop the limit from being managed."""
    from homeassistant.exceptions import HomeAssistantError

    def _boom(call: ServiceCall) -> None:
        raise HomeAssistantError("push gateway down")

    hass.services.async_register("notify", "mobile_app_phone", _boom)
    set_vehicle(hass, soc=80, limit=75)
    _, coordinator = await setup_entry(
        hass, make_entry(data={CONF_NOTIFY_SERVICE: "notify.mobile_app_phone"})
    )

    assert len(set_value_calls) == 1
    assert coordinator.phase is not None


async def test_messages_follow_the_configured_language(
    hass: HomeAssistant, notifications: list[ServiceCall]
) -> None:
    """Italian installations get Italian notifications."""
    hass.config.language = "it"
    set_vehicle(hass, soc=80, limit=75)
    await setup_entry(hass)

    assert "calibrazione" in notifications[0].data["title"]


def test_unknown_languages_fall_back_to_english() -> None:
    """Missing translations never raise."""
    title, _ = render("xx-YY", "calibration_started", vehicle="Car", since="", limit=100)
    assert "calibration started" in title
    assert fragment("de", "restored", daily=75).startswith("The limit")


def test_every_message_has_the_same_keys_in_every_language() -> None:
    """Keep the language tables in sync."""
    from custom_components.ev_lfp_coach import messages

    assert set(messages.MESSAGES["en"]) == set(messages.MESSAGES["it"])
    assert set(messages.FRAGMENTS["en"]) == set(messages.FRAGMENTS["it"])
