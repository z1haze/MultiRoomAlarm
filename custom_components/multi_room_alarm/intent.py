"""Intent platform — sentence-matched intents for the alarm clock.

This module is auto-discovered by HA's `intent` integration (any
`custom_components/<domain>/intent.py` is treated as an intent platform), which
calls `async_setup_intents(hass)` below. Sentence intents receive
`intent_obj.device_id`, so the room is resolved per-command with no global state.
Sentences live in custom_sentences/en/multi_room_alarm.yaml (installed on setup).
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from .const import DATA_ENGINE, DOMAIN
from .coordinator import AlarmClockEngine, schedule_phrase

_LOGGER = logging.getLogger(__name__)


def _engine(hass: HomeAssistant) -> AlarmClockEngine | None:
    return (hass.data.get(DOMAIN) or {}).get(DATA_ENGINE)


async def async_setup_intents(hass: HomeAssistant) -> None:
    """Called by the `intent` integration when this platform is discovered."""
    intent.async_register(hass, AlarmListIntent())
    intent.async_register(hass, AlarmStopIntent())
    intent.async_register(hass, AlarmSnoozeIntent())


def _speak_alarms(engine: AlarmClockEngine, room: str) -> str:
    alarms = engine.alarms_for_room(room)
    if not alarms:
        return "You have no alarms set."
    parts = []
    for a in alarms:
        hh, mm = a["time"].split(":")
        hh = int(hh)
        ap = "AM" if hh < 12 else "PM"
        phrase = f"{hh % 12 or 12}:{mm} {ap}"
        sched = schedule_phrase(a)
        if sched:
            phrase += f" {sched}"
        if not a.get("enabled", True):
            phrase += " (off)"
        parts.append(phrase)
    if len(parts) == 1:
        return f"You have one alarm: {parts[0]}."
    return f"You have {len(parts)} alarms: " + ", ".join(parts[:-1]) + f", and {parts[-1]}."


class AlarmListIntent(intent.IntentHandler):
    """'show me my alarms' — navigate the room's screen to its alarms page,
    or speak the list if the room has no screen configured."""

    intent_type = "AlarmList"
    description = "Show the alarms page for the current room."

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        engine = _engine(hass)
        response = intent_obj.create_response()
        if engine is None:
            response.async_set_speech("The alarm clock isn't set up.")
            return response
        room = engine.resolve_room_for_device(intent_obj.device_id)
        if room is None:
            response.async_set_speech("I couldn't tell which room to check.")
            return response

        cfg = engine.room(room) or {}
        browser = cfg.get("nav_browser")
        if browser:
            try:
                await hass.services.async_call(
                    "browser_mod", "navigate",
                    {"browser_id": browser, "path": f"/{room}-dashboard/alarms"},
                    blocking=False,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("browser_mod.navigate failed")
            has = bool(engine.alarms_for_room(room))
            response.async_set_speech(
                "Here are your alarms." if has else "You have no alarms set."
            )
        else:
            response.async_set_speech(_speak_alarms(engine, room))
        return response


class AlarmStopIntent(intent.IntentHandler):
    intent_type = "AlarmStop"
    description = "Stop the alarm going off in the current room."

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        engine = _engine(hass)
        response = intent_obj.create_response()
        if engine is None:
            response.async_set_speech("The alarm clock isn't set up.")
            return response
        room = engine.resolve_room_for_device(intent_obj.device_id)
        if room is None:
            response.async_set_speech("I couldn't tell which room.")
            return response
        if engine.ringing(room) is None and engine.snoozed(room) is None:
            response.async_set_speech("There's no alarm to stop.")
            return response
        await engine.async_stop(room)
        response.async_set_speech("Alarm stopped.")
        return response


class AlarmSnoozeIntent(intent.IntentHandler):
    intent_type = "AlarmSnooze"
    description = "Snooze the alarm going off in the current room."
    slot_schema = {vol.Optional("minutes"): str}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        engine = _engine(hass)
        slots = self.async_validate_slots(intent_obj.slots)
        response = intent_obj.create_response()
        if engine is None:
            response.async_set_speech("The alarm clock isn't set up.")
            return response
        room = engine.resolve_room_for_device(intent_obj.device_id)
        if room is None:
            response.async_set_speech("I couldn't tell which room.")
            return response
        if engine.ringing(room) is None:
            response.async_set_speech("There's no alarm going off.")
            return response

        minutes = None
        raw = slots.get("minutes", {}).get("value")
        if raw:
            try:
                minutes = int(str(raw).strip())
            except (TypeError, ValueError):
                minutes = None
        info = await engine.async_snooze(room, minutes)
        wake = (info or {}).get("wake_time")
        response.async_set_speech(f"Snoozed until {wake}." if wake else "Snoozed.")
        return response
