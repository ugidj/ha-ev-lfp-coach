# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-24

First public release, **alpha**: versions stay below 1.0.0 until a full cycle has been confirmed on real cars. Developed against a Tesla Model 3's entities and tested with a simulated-vehicle suite; a full real charge cycle is still to be confirmed.

### Highlights
- Everything optional (cable, location, charging power) lives in a collapsed *Optional settings*
  section. A new **"Calibrate only when at home"** switch (off by default) decides whether the
  location matters at all; without it the calibration also runs away from home.
- Minimum time at the top defaults to 30 min.

### Added
- Config flow with vehicle entity selectors, validation, one coach per charge limit entity
  (`unique_id`) and a **reconfigure** flow to follow a car whose entities changed.
- Options flow (reloads automatically) for every parameter, including the "charging statuses
  meaning finished" list, minimum time at the top, safety timeout, buffer, abort-after-hours,
  restore-after-manual-charge and the notification target.
- Calibration state machine: `calibrated` / `calibration_due` → `calibrating_100` →
  `top_balancing` → back to the daily limit, fully persisted so Home Assistant restarts and
  reloads resume the running phase.
- Entities: days since calibration, calibration state (enum), last / next calibration,
  calibration due, calibration in progress, three buttons (calibrate on next charge, cancel,
  mark calibrated now), three numbers (daily limit, calibration limit, interval) and an
  automatic-management switch.
- Services `ev_lfp_coach.start_calibration`, `cancel_calibration` and `mark_calibrated`.
- Bus events `ev_lfp_coach_calibration_started`, `_balancing_started`, `_calibration_completed`
  and `_calibration_aborted`.
- Repairs issues when the charge limit cannot be set repeatedly or a configured entity vanishes.
- Diagnostics download (notify target redacted), calibration history attribute.
- English and Italian translations, localized notifications, local brand images.

### Top balancing follows the current drawn by the car
- Optional **charging power** sensor (W or kW, e.g. a wall box). When set, top balancing ends only
  when the power stays at or below a threshold (default 50 W) for a set time (default 3 min) after
  the minimum hold; the charging status is ignored for that decision. An `unavailable` power
  sensor is never read as 0 W.

### Safety rules built in
- `unavailable` / `unknown` / non numeric states never count as 0, "unplugged" or "not home".
- A "complete" report is ignored until the minimum time at the top has passed; unplugging ends
  the phase at once; a timeout always restores the daily limit.
- Charge limit commands are serialized, de-duplicated while the vehicle catches up, retried
  with a back-off and verified through the entity state.
- The calibration interval counts calendar days (due from local midnight of day N), so cycles
  finishing in the evening do not drift by a day each time.
