"""Coordinator and calibration state machine for EV LFP Battery Coach.

The coordinator owns the whole workflow:

``idle`` (calibrated / calibration_due)
    -> ``calibrating_100``  the coach raised the charge limit and waits for the
                            battery to reach it
    -> ``top_balancing``    the battery is full; the limit is left alone while
                            the BMS balances the cells
    -> ``idle``             the coach restored the daily limit

Design rules:

* Decisions are made only on trustworthy data. ``unavailable``/``unknown`` is
  never interpreted as a number, a disconnected cable or "not at home".
* Every evaluation runs under a lock, so bursts of state changes can neither
  duplicate commands nor race the timers.
* All time based transitions are re-derived from persisted timestamps, so a
  restart (or an options reload) in the middle of a phase resumes it.
* Charge limit commands are verified-by-grace, retried with a back-off and
  reported through Repairs when they keep failing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
import voluptuous as vol

from . import messages
from .const import (
    CABLE_CONNECTED_STATES,
    CABLE_DISCONNECTED_STATES,
    CALIBRATION_STATES,
    COMMAND_CONFIRM_GRACE_SECONDS,
    COMMAND_RETRY_SECONDS,
    COMMAND_TIMEOUT_SECONDS,
    CONF_ABORT_AFTER_HOURS,
    CONF_BATTERY_LEVEL_ENTITY,
    CONF_CABLE_ENTITY,
    CONF_CALIBRATION_INTERVAL_DAYS,
    CONF_CALIBRATION_LIMIT,
    CONF_CHARGE_LIMIT_ENTITY,
    CONF_CHARGING_POWER_ENTITY,
    CONF_CHARGING_STATUS_ENTITY,
    CONF_DAILY_LIMIT,
    CONF_FINISHED_STATUSES,
    CONF_HOME_ONLY,
    CONF_HOME_ZONE,
    CONF_IDLE_MINUTES,
    CONF_IDLE_POWER_WATTS,
    CONF_LOCATION_ENTITY,
    CONF_MIN_HOLD_MINUTES,
    CONF_NOTIFY_SERVICE,
    CONF_RESTORE_AFTER_MANUAL,
    CONF_SEND_NOTIFICATIONS,
    CONF_TOP_BALANCING_BUFFER_SECONDS,
    CONF_TOP_BALANCING_TIMEOUT_MINUTES,
    DEFAULT_ABORT_AFTER_HOURS,
    DEFAULT_CALIBRATION_INTERVAL_DAYS,
    DEFAULT_CALIBRATION_LIMIT,
    DEFAULT_DAILY_LIMIT,
    DEFAULT_FINISHED_STATUSES,
    DEFAULT_HOME_ONLY,
    DEFAULT_HOME_ZONE,
    DEFAULT_IDLE_MINUTES,
    DEFAULT_IDLE_POWER_WATTS,
    DEFAULT_MIN_HOLD_MINUTES,
    DEFAULT_RESTORE_AFTER_MANUAL,
    DEFAULT_SEND_NOTIFICATIONS,
    DEFAULT_TOP_BALANCING_BUFFER_SECONDS,
    DEFAULT_TOP_BALANCING_TIMEOUT_MINUTES,
    DOMAIN,
    EVENT_BALANCING_STARTED,
    EVENT_CALIBRATION_ABORTED,
    EVENT_CALIBRATION_COMPLETED,
    EVENT_CALIBRATION_STARTED,
    FAILURES_BEFORE_ISSUE,
    HISTORY_LENGTH,
    ISSUE_ENTITY_MISSING,
    ISSUE_SET_LIMIT_FAILED,
    REACH_TOLERANCE,
    REASON_AWAY,
    REASON_MANUAL,
    REASON_POWER,
    REASON_STATUS,
    REASON_TIMEOUT,
    REASON_UNPLUGGED,
    SNOOZE_HOURS,
    STATE_CALIBRATED,
    STATE_CALIBRATING_100,
    STATE_CALIBRATION_DUE,
    STATE_CHANGE_DEBOUNCE_SECONDS,
    STATE_TOP_BALANCING,
    STORAGE_KEY,
    STORAGE_VERSION,
    UNPLUGGED_STATUSES,
    UPDATE_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

type EvLfpConfigEntry = ConfigEntry[EvLfpCoordinator]

_INVALID_STATES = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN, "", "none"})


@dataclass(frozen=True, slots=True)
class CoachData:
    """Immutable snapshot the entities render from."""

    state: str
    days_since_calibration: float | None
    last_calibration: datetime | None
    next_calibration: datetime | None
    calibration_due: bool
    active: bool
    enabled: bool
    force_next: bool
    phase_started: datetime | None
    daily_limit: int
    calibration_limit: int
    calibration_interval_days: int
    history: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """Normalized view of the vehicle; ``None`` always means "not known"."""

    soc: float | None
    limit: int | None
    status: str | None
    plugged: bool | None
    home: bool | None
    power_w: float | None = None

    @property
    def usable(self) -> bool:
        """Return True when SoC and limit can be trusted."""
        return self.soc is not None and self.limit is not None


async def async_cleanup_entry(hass: HomeAssistant, entry_id: str) -> None:
    """Delete persisted data and Repairs issues of a removed entry."""
    await Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry_id}").async_remove()
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_SET_LIMIT_FAILED}_{entry_id}")
    for key in (
        CONF_BATTERY_LEVEL_ENTITY,
        CONF_CHARGE_LIMIT_ENTITY,
        CONF_CHARGING_STATUS_ENTITY,
        CONF_CABLE_ENTITY,
        CONF_LOCATION_ENTITY,
        CONF_CHARGING_POWER_ENTITY,
    ):
        ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_ENTITY_MISSING}_{entry_id}_{key}")


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO timestamp from storage, tolerating garbage."""
    if not isinstance(value, str):
        return None
    try:
        parsed = dt_util.parse_datetime(value)
    except ValueError:
        return None
    if parsed is None:
        return None
    return dt_util.as_utc(parsed)


def _valid(state: State | None) -> State | None:
    """Return the state only when it carries a usable value."""
    if state is None or state.state.strip().lower() in _INVALID_STATES:
        return None
    return state


class EvLfpCoordinator(DataUpdateCoordinator[CoachData]):
    """Run the LFP calibration workflow for a single vehicle."""

    config_entry: EvLfpConfigEntry

    def __init__(self, hass: HomeAssistant, entry: EvLfpConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}"
        )

        # Persisted state
        self.last_100: datetime | None = None
        self.force_next: bool = False
        self.enabled: bool = True
        self.phase: str | None = None  # STATE_CALIBRATING_100 / STATE_TOP_BALANCING
        self.phase_started: datetime | None = None
        self.started_by_coach: bool = False
        self.unplugged_since: datetime | None = None
        self.suppress_until: datetime | None = None
        self.latched: bool = False
        self.history: list[dict[str, Any]] = []

        # Runtime state
        self._lock = asyncio.Lock()
        self._started = False
        self._finish_at: datetime | None = None
        self._idle_since: datetime | None = None
        self._pending: tuple[int, datetime] | None = None
        self._cmd_retry_after: datetime | None = None
        self._failures = 0
        self._wakes: list[datetime] = []
        self._wake_unsub: CALLBACK_TYPE | None = None
        self._missing: set[str] = set()
        self._debouncer: Debouncer[Any] = Debouncer(
            hass,
            _LOGGER,
            cooldown=STATE_CHANGE_DEBOUNCE_SECONDS,
            immediate=False,
            function=self.async_refresh,
        )

    # -------------------------------------------------------------------------
    # Configuration (options override data)
    # -------------------------------------------------------------------------
    def _get(self, key: str, default: Any = None) -> Any:
        """Return an option, falling back to entry data and then the default."""
        entry = self.config_entry
        if key in entry.options:
            return entry.options[key]
        return entry.data.get(key, default)

    def _entity(self, key: str) -> str | None:
        value = self._get(key)
        return value if isinstance(value, str) and value else None

    @property
    def vehicle_name(self) -> str:
        """Return the vehicle display name."""
        return str(self.config_entry.data.get(CONF_NAME) or self.config_entry.title)

    @property
    def battery_level_entity(self) -> str | None:
        """Battery percentage sensor."""
        return self._entity(CONF_BATTERY_LEVEL_ENTITY)

    @property
    def charge_limit_entity(self) -> str | None:
        """Charge limit number."""
        return self._entity(CONF_CHARGE_LIMIT_ENTITY)

    @property
    def charging_status_entity(self) -> str | None:
        """Charging status sensor."""
        return self._entity(CONF_CHARGING_STATUS_ENTITY)

    @property
    def cable_entity(self) -> str | None:
        """Optional cable sensor."""
        return self._entity(CONF_CABLE_ENTITY)

    @property
    def location_entity(self) -> str | None:
        """Optional device tracker / person."""
        return self._entity(CONF_LOCATION_ENTITY)

    @property
    def power_entity(self) -> str | None:
        """Optional sensor with the power the car draws (wall box or car)."""
        return self._entity(CONF_CHARGING_POWER_ENTITY)

    @property
    def idle_power_watts(self) -> float:
        """Power at or below which the car counts as not drawing current."""
        return float(self._get(CONF_IDLE_POWER_WATTS, DEFAULT_IDLE_POWER_WATTS))

    @property
    def idle_minutes(self) -> int:
        """Minutes the power must stay idle before balancing counts as finished."""
        return int(self._get(CONF_IDLE_MINUTES, DEFAULT_IDLE_MINUTES))

    @property
    def home_only(self) -> bool:
        """Whether calibrations are restricted to the home zone."""
        return bool(self._get(CONF_HOME_ONLY, DEFAULT_HOME_ONLY)) and bool(self.location_entity)

    @property
    def home_zone(self) -> str:
        """Zone that counts as home."""
        return self._entity(CONF_HOME_ZONE) or DEFAULT_HOME_ZONE

    @property
    def daily_limit(self) -> int:
        """Daily charge limit."""
        return int(self._get(CONF_DAILY_LIMIT, DEFAULT_DAILY_LIMIT))

    @property
    def calibration_limit(self) -> int:
        """Calibration charge limit."""
        return int(self._get(CONF_CALIBRATION_LIMIT, DEFAULT_CALIBRATION_LIMIT))

    @property
    def calibration_interval_days(self) -> int:
        """Days between calibrations."""
        return int(self._get(CONF_CALIBRATION_INTERVAL_DAYS, DEFAULT_CALIBRATION_INTERVAL_DAYS))

    @property
    def min_hold_minutes(self) -> int:
        """Minimum time spent at the top before a status can end balancing."""
        return int(self._get(CONF_MIN_HOLD_MINUTES, DEFAULT_MIN_HOLD_MINUTES))

    @property
    def top_balancing_timeout_minutes(self) -> int:
        """Maximum time spent at the top."""
        return int(
            self._get(
                CONF_TOP_BALANCING_TIMEOUT_MINUTES,
                DEFAULT_TOP_BALANCING_TIMEOUT_MINUTES,
            )
        )

    @property
    def top_balancing_buffer_seconds(self) -> int:
        """Delay between "finished" and restoring the daily limit."""
        return int(
            self._get(CONF_TOP_BALANCING_BUFFER_SECONDS, DEFAULT_TOP_BALANCING_BUFFER_SECONDS)
        )

    @property
    def finished_statuses(self) -> frozenset[str]:
        """Charging status values meaning the vehicle stopped charging."""
        raw = self._get(CONF_FINISHED_STATUSES, DEFAULT_FINISHED_STATUSES)
        if not isinstance(raw, (list, tuple, set, frozenset)):
            raw = DEFAULT_FINISHED_STATUSES
        return frozenset(str(item).strip().lower() for item in raw if str(item).strip())

    @property
    def abort_after_hours(self) -> int:
        """Hours away/unplugged after which a pending calibration is abandoned."""
        return int(self._get(CONF_ABORT_AFTER_HOURS, DEFAULT_ABORT_AFTER_HOURS))

    @property
    def restore_after_manual(self) -> bool:
        """Restore the daily limit also after a charge the user started."""
        return bool(self._get(CONF_RESTORE_AFTER_MANUAL, DEFAULT_RESTORE_AFTER_MANUAL))

    @property
    def send_notifications(self) -> bool:
        """Whether notifications are enabled."""
        return bool(self._get(CONF_SEND_NOTIFICATIONS, DEFAULT_SEND_NOTIFICATIONS))

    @property
    def notify_service(self) -> str | None:
        """Notify service or entity (None = persistent notification)."""
        value = self._get(CONF_NOTIFY_SERVICE)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @property
    def tracked_entities(self) -> dict[str, str]:
        """Map option key -> configured entity id."""
        keys = (
            CONF_BATTERY_LEVEL_ENTITY,
            CONF_CHARGE_LIMIT_ENTITY,
            CONF_CHARGING_STATUS_ENTITY,
            CONF_CABLE_ENTITY,
            CONF_LOCATION_ENTITY,
            CONF_CHARGING_POWER_ENTITY,
        )
        return {
            key: entity
            for key in keys
            if (entity := self._entity(key)) and (key != CONF_LOCATION_ENTITY or self.home_only)
        }

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------
    async def async_load(self) -> None:
        """Load persisted state."""
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return
        self.last_100 = _parse_datetime(stored.get("last_100"))
        self.force_next = bool(stored.get("force_next", False))
        self.enabled = bool(stored.get("enabled", True))
        phase = stored.get("phase")
        self.phase = phase if phase in (STATE_CALIBRATING_100, STATE_TOP_BALANCING) else None
        self.phase_started = _parse_datetime(stored.get("phase_started"))
        if self.phase and self.phase_started is None:
            self.phase_started = dt_util.utcnow()
        self.started_by_coach = bool(stored.get("started_by_coach", False))
        self.unplugged_since = _parse_datetime(stored.get("unplugged_since"))
        self.suppress_until = _parse_datetime(stored.get("suppress_until"))
        self.latched = bool(stored.get("latched", False))
        history = stored.get("history")
        if isinstance(history, list):
            self.history = [item for item in history if isinstance(item, dict)][-HISTORY_LENGTH:]

    @callback
    def async_start(self) -> None:
        """Start reacting to the vehicle once Home Assistant is fully started."""
        tracked = list(self.tracked_entities.values())
        if tracked:
            self.config_entry.async_on_unload(
                async_track_state_change_event(self.hass, tracked, self._handle_entity_change)
            )
        self.config_entry.async_on_unload(async_at_started(self.hass, self._async_handle_started))

    async def _async_handle_started(self, _hass: HomeAssistant) -> None:
        """Enable decisions: before this the vehicle entities may not exist yet."""
        self._started = True
        await self.async_refresh()

    async def async_shutdown(self) -> None:
        """Cancel timers and listeners."""
        await super().async_shutdown()
        self._debouncer.async_shutdown()
        if self._wake_unsub:
            self._wake_unsub()
            self._wake_unsub = None

    async def _async_save(self) -> None:
        """Persist state."""
        await self._store.async_save(
            {
                "last_100": self.last_100.isoformat() if self.last_100 else None,
                "force_next": self.force_next,
                "enabled": self.enabled,
                "phase": self.phase,
                "phase_started": self.phase_started.isoformat() if self.phase_started else None,
                "started_by_coach": self.started_by_coach,
                "unplugged_since": self.unplugged_since.isoformat()
                if self.unplugged_since
                else None,
                "suppress_until": self.suppress_until.isoformat() if self.suppress_until else None,
                "latched": self.latched,
                "history": self.history,
            }
        )

    # -------------------------------------------------------------------------
    # Derived values
    # -------------------------------------------------------------------------
    @property
    def state(self) -> str:
        """Return the workflow state exposed by the state sensor."""
        if self.phase:
            return self.phase
        return STATE_CALIBRATION_DUE if self.is_calibration_due else STATE_CALIBRATED

    @property
    def days_since_last_calibration(self) -> float | None:
        """Days since the last full charge, or None if there is none on record."""
        if self.last_100 is None:
            return None
        return round((dt_util.utcnow() - self.last_100).total_seconds() / 86400, 1)

    @property
    def next_calibration(self) -> datetime | None:
        """Moment from which a calibration is due.

        Calibration becomes due at local midnight of the Nth day after the last
        one, not N*24h later. Calibrations normally finish in the evening, so a
        strict 24h count would push every cycle one day further.
        """
        if self.last_100 is None:
            return None
        last_day: date = dt_util.as_local(self.last_100).date()
        return dt_util.start_of_local_day(last_day + timedelta(days=self.calibration_interval_days))

    @property
    def is_calibration_due(self) -> bool:
        """Return True when a calibration should happen at the next charge."""
        if self.force_next or self.last_100 is None:
            return True
        nxt = self.next_calibration
        return nxt is not None and dt_util.utcnow() >= nxt

    def _build_data(self) -> CoachData:
        return CoachData(
            state=self.state,
            days_since_calibration=self.days_since_last_calibration,
            last_calibration=self.last_100,
            next_calibration=self.next_calibration,
            calibration_due=self.is_calibration_due,
            active=self.phase is not None,
            enabled=self.enabled,
            force_next=self.force_next,
            phase_started=self.phase_started,
            daily_limit=self.daily_limit,
            calibration_limit=self.calibration_limit,
            calibration_interval_days=self.calibration_interval_days,
            history=tuple(self.history),
        )

    @callback
    def _publish(self) -> None:
        """Push the current snapshot to the entities without a new evaluation."""
        self.async_set_updated_data(self._build_data())

    async def _async_update_data(self) -> CoachData:
        """Evaluate the vehicle, then return the new snapshot."""
        await self._async_run()
        return self._build_data()

    # -------------------------------------------------------------------------
    # Vehicle snapshot
    # -------------------------------------------------------------------------
    def _number(self, entity_id: str | None) -> float | None:
        if not entity_id or (state := _valid(self.hass.states.get(entity_id))) is None:
            return None
        try:
            return float(state.state.replace(",", "."))
        except ValueError:
            return None

    def _power_watts(self) -> float | None:
        """Return the charging power in watts, honouring W / kW units."""
        entity_id = self.power_entity
        if not entity_id or (state := _valid(self.hass.states.get(entity_id))) is None:
            return None
        try:
            value = float(state.state.replace(",", "."))
        except ValueError:
            return None
        unit = str(state.attributes.get("unit_of_measurement", "W")).strip().lower()
        return value * 1000 if unit == "kw" else value

    def _plugged(self, status: str | None) -> bool | None:
        """Return whether the cable is connected; None when unknown."""
        if cable := self.cable_entity:
            state = _valid(self.hass.states.get(cable))
            if state is None:
                return None
            value = state.state.strip().lower()
            if value in CABLE_CONNECTED_STATES:
                return True
            if value in CABLE_DISCONNECTED_STATES:
                return False
            return None
        if status is None:
            return None
        return status not in UNPLUGGED_STATUSES

    def _at_home(self) -> bool | None:
        """Return whether the vehicle is home; True when location is not restricted."""
        if not self.home_only or not (tracker := self.location_entity):
            return True
        state = _valid(self.hass.states.get(tracker))
        if state is None:
            return None
        value = state.state.strip().lower()
        zone_id = self.home_zone
        if zone_id == DEFAULT_HOME_ZONE:
            return value == "home"
        zone = self.hass.states.get(zone_id)
        if zone is None:
            return None
        return value == str(zone.attributes.get("friendly_name", zone.name)).lower()

    def _snapshot(self) -> _Snapshot:
        status_state = _valid(
            self.hass.states.get(self.charging_status_entity)
            if self.charging_status_entity
            else None
        )
        status = status_state.state.strip().lower() if status_state else None
        limit = self._number(self.charge_limit_entity)
        return _Snapshot(
            soc=self._number(self.battery_level_entity),
            limit=None if limit is None else round(limit),
            status=status,
            plugged=self._plugged(status),
            home=self._at_home(),
            power_w=self._power_watts(),
        )

    # -------------------------------------------------------------------------
    # Triggers
    # -------------------------------------------------------------------------
    @callback
    def _handle_entity_change(self, _event: Event[EventStateChangedData]) -> None:
        """Debounce bursts of vehicle updates into one evaluation."""
        self._debouncer.async_schedule_call()

    def _wake_at(self, when: datetime) -> None:
        """Ask to be evaluated again at ``when`` (earliest request wins)."""
        self._wakes.append(when)

    def _apply_wakes(self) -> None:
        if self._wake_unsub:
            self._wake_unsub()
            self._wake_unsub = None
        now = dt_util.utcnow()
        future = [moment for moment in self._wakes if moment > now]
        self._wakes = []
        if future:
            self._wake_unsub = async_track_point_in_utc_time(
                self.hass, self._async_handle_wake, min(future)
            )

    async def _async_handle_wake(self, _now: datetime) -> None:
        self._wake_unsub = None
        await self.async_refresh()

    # -------------------------------------------------------------------------
    # State machine
    # -------------------------------------------------------------------------
    async def _async_run(self) -> None:
        """Run one serialized evaluation."""
        async with self._lock:
            try:
                self._check_entities()
                if self._started and self.enabled:
                    snap = self._snapshot()
                    _LOGGER.debug("Evaluating %s: %s phase=%s", self.vehicle_name, snap, self.phase)
                    if snap.usable:
                        now = dt_util.utcnow()
                        if self.phase == STATE_TOP_BALANCING:
                            await self._async_step_balancing(snap, now)
                        elif self.phase == STATE_CALIBRATING_100:
                            await self._async_step_calibrating(snap, now)
                        else:
                            await self._async_step_idle(snap, now)
                if self.next_calibration is not None and self.phase is None:
                    self._wake_at(self.next_calibration)
            except Exception:
                _LOGGER.exception("Unexpected error while evaluating %s", self.vehicle_name)
            finally:
                self._apply_wakes()

    def _reached(self, snap: _Snapshot) -> bool:
        """Return True when the battery sits at the calibration target."""
        assert snap.soc is not None and snap.limit is not None
        return (
            snap.soc >= self.calibration_limit - REACH_TOLERANCE
            and snap.limit >= self.calibration_limit
        )

    async def _async_step_idle(self, snap: _Snapshot, now: datetime) -> None:
        assert snap.limit is not None
        reached = self._reached(snap)
        if not reached and self.latched:
            self.latched = False
            await self._async_save()

        # A full charge the user started counts as a calibration too.
        if reached and not self.latched and snap.plugged is not False:
            _LOGGER.info(
                "%s reached %d%% outside a coach cycle", self.vehicle_name, self.calibration_limit
            )
            await self._async_enter_balancing(now, by_coach=False)
            return

        suppressed = self.suppress_until is not None and now < self.suppress_until
        if suppressed:
            assert self.suppress_until is not None
            self._wake_at(self.suppress_until)
        wanted = (
            self.is_calibration_due
            and not suppressed
            and snap.plugged is True
            and snap.home is True
            and snap.limit < self.calibration_limit
        )
        if not wanted or not await self._async_set_limit(self.calibration_limit):
            return

        days = self.days_since_last_calibration
        self.phase = STATE_CALIBRATING_100
        self.phase_started = now
        self.started_by_coach = True
        self.unplugged_since = None
        await self._async_save()
        if self.force_next:
            since = messages.fragment(self._language, "since_forced")
        elif days is None:
            since = messages.fragment(self._language, "since_never")
        else:
            since = messages.fragment(self._language, "since_days", days=f"{days:.0f}")
        self._fire(EVENT_CALIBRATION_STARTED, limit=self.calibration_limit)
        await self._async_notify(
            messages.MESSAGE_CALIBRATION_STARTED,
            since=since,
            limit=self.calibration_limit,
        )

    async def _async_step_calibrating(self, snap: _Snapshot, now: datetime) -> None:
        assert snap.limit is not None
        if self._reached(snap):
            await self._async_enter_balancing(now, by_coach=True)
            await self._async_step_balancing(snap, now)
            return

        away = snap.plugged is False or snap.home is False
        if away:
            if self.unplugged_since is None:
                self.unplugged_since = now
                await self._async_save()
            if self.abort_after_hours > 0:
                deadline = self.unplugged_since + timedelta(hours=self.abort_after_hours)
                if now >= deadline:
                    reason = REASON_UNPLUGGED if snap.plugged is False else REASON_AWAY
                    await self._async_end_phase(reason, completed=False)
                    return
                self._wake_at(deadline)
            return

        if self.unplugged_since is not None and snap.plugged is True:
            self.unplugged_since = None
            await self._async_save()

        # The coach owns the limit while calibrating: re-assert it if somebody
        # (or the car) lowered it. The in-flight guard prevents command spam.
        if snap.limit < self.calibration_limit:
            await self._async_set_limit(self.calibration_limit)

    async def _async_enter_balancing(self, now: datetime, *, by_coach: bool) -> None:
        self.last_100 = now
        self.force_next = False
        self.phase = STATE_TOP_BALANCING
        self.phase_started = now
        self.started_by_coach = by_coach or self.started_by_coach
        self.unplugged_since = None
        self._finish_at = None
        self._idle_since = None
        await self._async_save()
        self._fire(EVENT_BALANCING_STARTED, limit=self.calibration_limit)
        await self._async_notify(messages.MESSAGE_BALANCING_STARTED, limit=self.calibration_limit)

    async def _async_step_balancing(self, snap: _Snapshot, now: datetime) -> None:
        assert self.phase_started is not None
        elapsed = now - self.phase_started
        hold = timedelta(minutes=self.min_hold_minutes)
        timeout = timedelta(minutes=self.top_balancing_timeout_minutes)

        reason: str | None = None
        if snap.plugged is False:
            reason = REASON_UNPLUGGED
        elif elapsed >= timeout:
            reason = REASON_TIMEOUT
        elif self.power_entity:
            # The car's own draw is the source of truth: balancing is over only
            # when it has stopped absorbing current for a sustained time. The
            # status ("complete" can show up while the BMS still balances) is
            # deliberately ignored in this mode.
            idle = snap.power_w is not None and snap.power_w <= self.idle_power_watts
            if idle:
                if self._idle_since is None:
                    self._idle_since = now
                ready = max(
                    self._idle_since + timedelta(minutes=self.idle_minutes),
                    self.phase_started + hold,
                )
                if now >= ready:
                    reason = REASON_POWER
                else:
                    self._wake_at(ready)
            else:
                self._idle_since = None  # drawing current, or power unknown
        elif snap.status in self.finished_statuses:
            if elapsed >= hold:
                reason = REASON_STATUS
            else:
                self._wake_at(self.phase_started + hold)

        if reason is None:
            # Still charging (or nothing certain): forget a pending finish.
            self._finish_at = None
            self._wake_at(self.phase_started + timeout)
            return

        delay = 0 if reason == REASON_UNPLUGGED else self.top_balancing_buffer_seconds
        target = now + timedelta(seconds=delay)
        if self._finish_at is None or target < self._finish_at:
            self._finish_at = target
        if now < self._finish_at:
            self._wake_at(self._finish_at)
            return
        await self._async_end_phase(reason, completed=True)

    async def _async_end_phase(self, reason: str, *, completed: bool) -> bool:
        """Leave calibrating/top_balancing; restore the daily limit if needed.

        Returns False (and keeps the phase) when the limit could not be restored,
        so the next evaluation retries.
        """
        restore = self.started_by_coach or self.restore_after_manual
        if restore and not await self._async_set_limit(self.daily_limit):
            return False

        now = dt_util.utcnow()
        minutes = (
            round((now - self.phase_started).total_seconds() / 60, 1) if self.phase_started else 0.0
        )
        was_balancing = self.phase == STATE_TOP_BALANCING
        if completed and was_balancing:
            self.history = [
                *self.history,
                {
                    "completed": now.isoformat(),
                    "reason": reason,
                    "minutes_at_top": minutes,
                    "automatic": self.started_by_coach,
                },
            ][-HISTORY_LENGTH:]
        self.latched = was_balancing
        self.phase = None
        self.phase_started = None
        self.started_by_coach = False
        self.unplugged_since = None
        self._finish_at = None
        self._idle_since = None
        await self._async_save()

        outcome = messages.fragment(
            self._language, "restored" if restore else "left", daily=self.daily_limit
        )
        reason_text = messages.fragment(self._language, f"reason_{reason}")
        if completed:
            self._fire(EVENT_CALIBRATION_COMPLETED, reason=reason, minutes_at_top=minutes)
            await self._async_notify(
                messages.MESSAGE_CALIBRATION_COMPLETED,
                reason=reason_text,
                minutes=f"{minutes:.0f}",
                outcome=outcome,
            )
        else:
            self._fire(EVENT_CALIBRATION_ABORTED, reason=reason)
            await self._async_notify(
                messages.MESSAGE_CALIBRATION_ABORTED, reason=reason_text, outcome=outcome
            )
        return True

    # -------------------------------------------------------------------------
    # Vehicle commands
    # -------------------------------------------------------------------------
    async def _async_set_limit(self, target: int) -> bool:
        """Command the charge limit; True when the vehicle accepted (or already got) it."""
        entity_id = self.charge_limit_entity
        if not entity_id:
            return False
        now = dt_util.utcnow()
        if self._cmd_retry_after is not None and now < self._cmd_retry_after:
            self._wake_at(self._cmd_retry_after)
            return False
        if self._pending is not None and self._pending[0] == target and now < self._pending[1]:
            self._wake_at(self._pending[1])
            return True  # in flight: the entity has not reflected it yet

        _LOGGER.info("Setting %s to %d%%", entity_id, target)
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT_SECONDS):
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": entity_id, "value": target},
                    blocking=True,
                )
        except (HomeAssistantError, vol.Invalid, TimeoutError) as err:
            self._failures += 1
            self._cmd_retry_after = now + timedelta(seconds=COMMAND_RETRY_SECONDS)
            self._wake_at(self._cmd_retry_after)
            _LOGGER.warning(
                "Setting %s to %d%% failed (%d in a row): %s",
                entity_id,
                target,
                self._failures,
                err,
            )
            if self._failures == FAILURES_BEFORE_ISSUE:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    f"{ISSUE_SET_LIMIT_FAILED}_{self.config_entry.entry_id}",
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_SET_LIMIT_FAILED,
                    translation_placeholders={
                        "vehicle": self.vehicle_name,
                        "entity": entity_id,
                    },
                )
                await self._async_notify(messages.MESSAGE_SET_LIMIT_FAILED, entity=entity_id)
            return False

        if self._failures:
            ir.async_delete_issue(
                self.hass, DOMAIN, f"{ISSUE_SET_LIMIT_FAILED}_{self.config_entry.entry_id}"
            )
        self._failures = 0
        self._cmd_retry_after = None
        grace = now + timedelta(seconds=COMMAND_CONFIRM_GRACE_SECONDS)
        self._pending = (target, grace)
        self._wake_at(grace)
        return True

    # -------------------------------------------------------------------------
    # Notifications & events
    # -------------------------------------------------------------------------
    @property
    def _language(self) -> str:
        return self.hass.config.language

    @callback
    def _fire(self, event_type: str, **data: Any) -> None:
        self.hass.bus.async_fire(
            event_type,
            {
                "entry_id": self.config_entry.entry_id,
                "vehicle": self.vehicle_name,
                **data,
            },
        )

    async def _async_notify(self, key: str, **values: Any) -> None:
        """Send a localized notification; failures never break the workflow."""
        if not self.send_notifications:
            return
        title, message = messages.render(
            self._language,
            key,
            vehicle=self.vehicle_name,
            **{"since": "", "outcome": "", **values},
        )
        title = title.strip()
        message = message.strip()
        target = self.notify_service
        hass = self.hass
        try:
            if target and hass.states.get(target) is not None and target.startswith("notify."):
                await hass.services.async_call(
                    "notify",
                    "send_message",
                    {"entity_id": target, "title": title, "message": message},
                    blocking=False,
                )
                return
            if target:
                domain, _, service = target.rpartition(".")
                domain = domain or "notify"
                if hass.services.has_service(domain, service):
                    payload: dict[str, Any] = {"title": title, "message": message}
                    if service.startswith("mobile_app_"):
                        payload["data"] = {"tag": f"{DOMAIN}_{key}", "channel": "EV LFP Coach"}
                    await hass.services.async_call(domain, service, payload, blocking=False)
                    return
                _LOGGER.warning(
                    "Notify target %s not found, using a persistent notification", target
                )
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": title,
                    "message": message,
                    "notification_id": f"{DOMAIN}_{self.config_entry.entry_id}_{key}",
                },
                blocking=False,
            )
        except (HomeAssistantError, vol.Invalid) as err:
            _LOGGER.warning("Could not send notification %s: %s", key, err)

    # -------------------------------------------------------------------------
    # Repairs
    # -------------------------------------------------------------------------
    def _check_entities(self) -> None:
        """Raise a Repairs issue for configured entities that no longer exist."""
        if not self._started:
            return
        registry = er.async_get(self.hass)
        missing: set[str] = set()
        for key, entity_id in self.tracked_entities.items():
            if self.hass.states.get(entity_id) is None and registry.async_get(entity_id) is None:
                missing.add(key)
                if key not in self._missing:
                    ir.async_create_issue(
                        self.hass,
                        DOMAIN,
                        f"{ISSUE_ENTITY_MISSING}_{self.config_entry.entry_id}_{key}",
                        is_fixable=False,
                        severity=ir.IssueSeverity.ERROR,
                        translation_key=ISSUE_ENTITY_MISSING,
                        translation_placeholders={
                            "vehicle": self.vehicle_name,
                            "entity": entity_id,
                        },
                    )
        for key in self._missing - missing:
            ir.async_delete_issue(
                self.hass, DOMAIN, f"{ISSUE_ENTITY_MISSING}_{self.config_entry.entry_id}_{key}"
            )
        self._missing = missing

    # -------------------------------------------------------------------------
    # Public actions (buttons, switch, numbers, services)
    # -------------------------------------------------------------------------
    async def async_force_calibration(self) -> None:
        """Calibrate at the next opportunity, regardless of the elapsed time."""
        async with self._lock:
            self.force_next = True
            self.suppress_until = None
            await self._async_save()
        await self.async_refresh()

    async def async_cancel_calibration(self) -> None:
        """Abort the running calibration and stop asking for 100% for a while."""
        async with self._lock:
            self.force_next = False
            self.suppress_until = dt_util.utcnow() + timedelta(hours=SNOOZE_HOURS)
            if self.phase:
                await self._async_end_phase(REASON_MANUAL, completed=False)
            else:
                await self._async_save()
        await self.async_refresh()

    async def async_mark_calibrated(self, when: datetime | None = None) -> None:
        """Record a full charge now (or at ``when``) and finish any running cycle."""
        async with self._lock:
            self.last_100 = dt_util.as_utc(when) if when else dt_util.utcnow()
            self.force_next = False
            self.suppress_until = None
            if self.phase:
                await self._async_end_phase(REASON_MANUAL, completed=True)
            else:
                await self._async_save()
        await self.async_refresh()

    async def async_set_enabled(self, enabled: bool) -> None:
        """Enable or disable the automatic management."""
        async with self._lock:
            if not enabled and self.phase:
                await self._async_end_phase(REASON_MANUAL, completed=False)
            self.enabled = enabled
            await self._async_save()
        await self.async_refresh()

    async def async_set_parameter(self, key: str, value: int) -> None:
        """Change a numeric parameter from a number entity."""
        daily = value if key == CONF_DAILY_LIMIT else self.daily_limit
        calibration = value if key == CONF_CALIBRATION_LIMIT else self.calibration_limit
        if daily >= calibration:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="daily_limit_greater_than_calibration",
            )
        self.hass.config_entries.async_update_entry(
            self.config_entry, options={**self.config_entry.options, key: value}
        )
        self._publish()
        await self.async_refresh()

    # -------------------------------------------------------------------------
    # Diagnostics
    # -------------------------------------------------------------------------
    def diagnostics(self) -> dict[str, Any]:
        """Return internal state for the diagnostics download."""
        snap = self._snapshot()
        return {
            "state": self.state,
            "started": self._started,
            "enabled": self.enabled,
            "phase": self.phase,
            "phase_started": self.phase_started,
            "started_by_coach": self.started_by_coach,
            "last_100": self.last_100,
            "next_calibration": self.next_calibration,
            "force_next": self.force_next,
            "suppress_until": self.suppress_until,
            "unplugged_since": self.unplugged_since,
            "latched": self.latched,
            "pending_command": self._pending,
            "consecutive_failures": self._failures,
            "missing_entities": sorted(self._missing),
            "snapshot": {
                "soc": snap.soc,
                "limit": snap.limit,
                "status": snap.status,
                "plugged": snap.plugged,
                "home": snap.home,
                "power_w": snap.power_w,
            },
            "history": self.history,
            "known_states": CALIBRATION_STATES,
        }
