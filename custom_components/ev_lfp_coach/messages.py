"""Localized notification texts for EV LFP Battery Coach.

Home Assistant has no translation API for free-form notification bodies, so the
few messages the coach sends are kept here, keyed by language code. Unknown
languages fall back to English.
"""

from __future__ import annotations

from typing import Final

DEFAULT_LANGUAGE: Final = "en"

MESSAGE_CALIBRATION_STARTED: Final = "calibration_started"
MESSAGE_BALANCING_STARTED: Final = "balancing_started"
MESSAGE_CALIBRATION_COMPLETED: Final = "calibration_completed"
MESSAGE_CALIBRATION_ABORTED: Final = "calibration_aborted"
MESSAGE_SET_LIMIT_FAILED: Final = "set_limit_failed"

# {vehicle}, {limit}, {daily}, {days}, {minutes}, {reason}, {entity} are filled
# in by the coordinator.
MESSAGES: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "en": {
        MESSAGE_CALIBRATION_STARTED: (
            "⚡ {vehicle}: LFP calibration started",
            "{since} The charge limit was raised to {limit}% to calibrate the battery.",
        ),
        MESSAGE_BALANCING_STARTED: (
            "⚖️ {vehicle}: top balancing in progress",
            "The battery reached {limit}%. The limit stays put while the BMS balances the cells.",
        ),
        MESSAGE_CALIBRATION_COMPLETED: (
            "🔋 {vehicle}: LFP calibration completed",
            "Cell balancing finished ({reason}, {minutes} min at the top). {outcome}",
        ),
        MESSAGE_CALIBRATION_ABORTED: (
            "⚠️ {vehicle}: LFP calibration cancelled",
            "The calibration was cancelled ({reason}). It will be retried at the "
            "next charge. {outcome}",
        ),
        MESSAGE_SET_LIMIT_FAILED: (
            "❗ {vehicle}: cannot change the charge limit",
            "Setting {entity} failed several times in a row. Check that the car "
            "is reachable; the coach keeps retrying.",
        ),
    },
    "it": {
        MESSAGE_CALIBRATION_STARTED: (
            "⚡ {vehicle}: calibrazione LFP avviata",
            "{since} Il limite di carica è stato portato al {limit}% per calibrare la batteria.",
        ),
        MESSAGE_BALANCING_STARTED: (
            "⚖️ {vehicle}: bilanciamento celle in corso",
            "La batteria ha raggiunto il {limit}%. Il limite non viene toccato "
            "finché il BMS non ha bilanciato le celle.",
        ),
        MESSAGE_CALIBRATION_COMPLETED: (
            "🔋 {vehicle}: calibrazione LFP completata",
            "Bilanciamento celle terminato ({reason}, {minutes} min al massimo). {outcome}",
        ),
        MESSAGE_CALIBRATION_ABORTED: (
            "⚠️ {vehicle}: calibrazione LFP annullata",
            "La calibrazione è stata annullata ({reason}). Verrà ritentata alla "
            "prossima ricarica. {outcome}",
        ),
        MESSAGE_SET_LIMIT_FAILED: (
            "❗ {vehicle}: impossibile cambiare il limite di carica",
            "L'impostazione di {entity} è fallita più volte di fila. Controlla "
            "che l'auto sia raggiungibile; il coach continua a riprovare.",
        ),
    },
}

# Fragments interpolated into the messages above.
FRAGMENTS: Final[dict[str, dict[str, str]]] = {
    "en": {
        "since_days": "{days} days passed since the last full charge.",
        "since_never": "No full charge on record yet.",
        "since_forced": "Calibration was requested manually.",
        "restored": "The limit is back to {daily}%.",
        "left": "The charge limit was left untouched.",
        "reason_status": "charging finished",
        "reason_power": "the car stopped drawing power",
        "reason_timeout": "safety timeout",
        "reason_unplugged": "cable unplugged",
        "reason_manual": "manual request",
        "reason_away": "vehicle away from home",
    },
    "it": {
        "since_days": "Sono passati {days} giorni dall'ultima carica completa.",
        "since_never": "Nessuna carica completa registrata finora.",
        "since_forced": "La calibrazione è stata richiesta manualmente.",
        "restored": "Il limite è tornato al {daily}%.",
        "left": "Il limite di carica non è stato modificato.",
        "reason_status": "ricarica terminata",
        "reason_power": "l'auto ha smesso di assorbire corrente",
        "reason_timeout": "timeout di sicurezza",
        "reason_unplugged": "cavo scollegato",
        "reason_manual": "richiesta manuale",
        "reason_away": "veicolo lontano da casa",
    },
}


def _lang(language: str | None) -> str:
    """Return a supported language code for the given Home Assistant language."""
    short = (language or DEFAULT_LANGUAGE).split("-")[0].lower()
    return short if short in MESSAGES else DEFAULT_LANGUAGE


def fragment(language: str | None, key: str, **values: object) -> str:
    """Return a localized text fragment."""
    return FRAGMENTS[_lang(language)][key].format(**values)


def render(language: str | None, key: str, **values: object) -> tuple[str, str]:
    """Return the localized (title, message) pair for a message key."""
    title, message = MESSAGES[_lang(language)][key]
    return title.format(**values), message.format(**values)
