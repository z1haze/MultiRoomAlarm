"""LLM API — lets the conversation agent create/read/stop/snooze alarms.

The room is resolved server-side from the calling device (device -> area ->
the matching room). The model never passes a room.
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

from .const import DATA_ENGINE, DOMAIN, LLM_API_ID, LLM_API_NAME
from .coordinator import AlarmClockEngine, ValidationError, schedule_phrase

_LOGGER = logging.getLogger(__name__)
_UNREG_KEY = f"{DOMAIN}_llm_unregister"


def _engine(hass: HomeAssistant) -> AlarmClockEngine:
    return hass.data[DOMAIN][DATA_ENGINE]


def async_register_llm_api(hass: HomeAssistant) -> None:
    try:
        unreg = llm.async_register_api(
            hass, AlarmClockAPI(hass=hass, id=LLM_API_ID, name=LLM_API_NAME)
        )
    except Exception as err:  # noqa: BLE001  e.g. already registered
        _LOGGER.debug("LLM API register skipped: %s", err)
        return
    if callable(unreg):
        hass.data.setdefault(DOMAIN, {})[_UNREG_KEY] = unreg


def async_unregister_llm_api(hass: HomeAssistant) -> None:
    unreg = hass.data.get(DOMAIN, {}).pop(_UNREG_KEY, None)
    if callable(unreg):
        unreg()


class _RoomTool(llm.Tool):
    """Base: resolves the room from the calling device."""

    def _room(self, hass: HomeAssistant, llm_context: llm.LLMContext) -> str | None:
        return _engine(hass).resolve_room_for_device(llm_context.device_id)


class AlarmSetTool(_RoomTool):
    name = "alarm_set"
    description = (
        "Set an alarm for the room the user is currently in (detected "
        "automatically from the device — never ask which room). `time` is "
        "24-hour HH:MM. For a repeating alarm pass `days` as comma-separated "
        "weekday abbreviations (mon,tue,wed,thu,fri,sat,sun). For a one-time "
        "alarm pass `date` as YYYY-MM-DD instead of days (compute it from "
        "'tomorrow', 'this Saturday', etc.). Optionally pass `sound`: a song, "
        "artist, or playlist name to wake up to."
    )
    parameters = vol.Schema(
        {
            vol.Required("time"): str,
            vol.Optional("days"): str,
            vol.Optional("date"): str,
            vol.Optional("sound"): str,
            vol.Optional("sound_type"): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        room = self._room(hass, llm_context)
        if room is None:
            return {"error": "Could not determine which room this alarm is for."}
        args = tool_input.tool_args or {}
        try:
            alarm = await _engine(hass).async_add_alarm(
                time=args["time"],
                room=room,
                days=args.get("days"),
                date=args.get("date"),
                sound=args.get("sound"),
                sound_type=args.get("sound_type"),
            )
        except ValidationError as err:
            return {"error": str(err)}
        return {"success": True, "alarm": _engine(hass).alarm_summary(alarm)}


class AlarmListTool(_RoomTool):
    name = "alarm_list"
    description = (
        "List the alarms set for the room the user is currently in. Takes no "
        "arguments. Returns each alarm's time, schedule and whether it's enabled."
    )
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        room = self._room(hass, llm_context)
        if room is None:
            return {"error": "Could not determine which room to check."}
        alarms = _engine(hass).alarms_for_room(room)
        return {
            "room": room,
            "count": len(alarms),
            "alarms": [
                {
                    "id": a.get("id"),
                    "time": a.get("time"),
                    "schedule": schedule_phrase(a) or "no days",
                    "enabled": a.get("enabled", True),
                    "sound": a.get("sound"),
                }
                for a in alarms
            ],
        }


class AlarmStopTool(_RoomTool):
    name = "alarm_stop"
    description = (
        "Stop / dismiss the alarm going off in the user's current room. Also "
        "cancels a snoozed alarm. Takes no arguments."
    )
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        room = self._room(hass, llm_context)
        if room is None:
            return {"error": "Could not determine which room."}
        engine = _engine(hass)
        if engine.ringing(room) is None and engine.snoozed(room) is None:
            return {"stopped": False, "reason": "no alarm active"}
        await engine.async_stop(room)
        return {"stopped": True}


class AlarmSnoozeTool(_RoomTool):
    name = "alarm_snooze"
    description = (
        "Snooze the alarm going off in the user's current room — silence it "
        "now and it rings again after a few minutes. Optionally pass `minutes` "
        "to override the default snooze length."
    )
    parameters = vol.Schema({vol.Optional("minutes"): vol.Coerce(int)})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        room = self._room(hass, llm_context)
        if room is None:
            return {"error": "Could not determine which room."}
        engine = _engine(hass)
        if engine.ringing(room) is None:
            return {"snoozed": False, "reason": "no alarm ringing"}
        info = await engine.async_snooze(room, (tool_input.tool_args or {}).get("minutes"))
        return {"snoozed": True, "wake_time": (info or {}).get("wake_time")}


class AlarmClockAPI(llm.API):
    """LLM API exposing the alarm-clock tools."""

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        prompt = (
            "Alarm-clock tools for the user's current room: alarm_set (create), "
            "alarm_list (read), alarm_stop (dismiss the ringing alarm), "
            "alarm_snooze (silence it for a few minutes). Do time/day parsing "
            "yourself and pass structured values."
        )
        engine = _engine(self.hass)
        room = engine.resolve_room_for_device(llm_context.device_id)
        if room:
            if engine.ringing(room) is not None:
                prompt += (
                    " AN ALARM IS CURRENTLY RINGING in the user's room. If they "
                    'say anything meaning stop/dismiss ("stop", "okay", "enough", '
                    '"I\'m up", "turn it off") call alarm_stop. If they ask for '
                    'more time ("snooze", "five more minutes") call alarm_snooze.'
                )
            elif engine.snoozed(room) is not None:
                prompt += ' An alarm in the user\'s room is snoozed; "stop" cancels it.'
        return llm.APIInstance(
            api=self,
            api_prompt=prompt,
            llm_context=llm_context,
            tools=[AlarmSetTool(), AlarmListTool(), AlarmStopTool(), AlarmSnoozeTool()],
        )
