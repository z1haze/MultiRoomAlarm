"""LLM API — lets the conversation agent create/read/stop/snooze alarms.

Stop and snooze act on the calling device's room (device -> area -> the
matching room). Set and list take an optional spoken `room` that overrides it;
their descriptions list the room names, rebuilt per conversation.
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

from .const import DOMAIN, LLM_API_ID, LLM_API_NAME
from .coordinator import AlarmClockEngine, ValidationError, get_engine, schedule_phrase

_LOGGER = logging.getLogger(__name__)
_UNREG_KEY = f"{DOMAIN}_llm_unregister"


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
    """Base: resolves the engine and the calling device's room."""

    @staticmethod
    def _resolve(
        hass: HomeAssistant, llm_context: llm.LLMContext
    ) -> tuple[AlarmClockEngine | None, str | None]:
        engine = get_engine(hass)
        room = engine.resolve_room_for_device(llm_context.device_id) if engine else None
        return engine, room


class _NamedRoomTool(_RoomTool):
    """Base for tools that take an optional spoken `room`. The description
    template's `{rooms}` is filled with the current room names."""

    _description: str

    def __init__(self, room_choices: list[str]) -> None:
        self.description = self._description.format(rooms=_join(room_choices))

    @staticmethod
    def _resolve_named(
        hass: HomeAssistant, llm_context: llm.LLMContext, spoken: str | None
    ) -> tuple[AlarmClockEngine | None, str | None, str | None]:
        """(engine, room id, error). A named room wins over the device's room;
        with neither, the error lists the rooms so the model can ask."""
        engine, device_room = _RoomTool._resolve(hass, llm_context)
        if engine is None:
            return None, None, "The alarm clock isn't set up."
        rooms = _join(engine.room_choices())
        if spoken and spoken.strip():
            room = engine.resolve_room_by_name(spoken)
            if room is None:
                return engine, None, (
                    f"There's no room called {spoken.strip()!r}. The rooms are: "
                    f"{rooms}. If the user meant one of these, call again with "
                    "its name or area name exactly; otherwise ask them which one."
                )
            return engine, room, None
        if device_room is None:
            return engine, None, (
                f"Which room? This device isn't in one. The rooms are: {rooms}. "
                "Ask the user which one."
            )
        return engine, device_room, None


def _join(names: list[str]) -> str:
    return ", ".join(names) or "(none configured)"


_ROOM_ARG = (
    "`room` is optional. Whenever the user names a room or place, pass what "
    "they said, even if it isn't one of the rooms — the rooms are: {rooms} "
    "(a room's name or its area name both work). Leave it out only when the "
    "user names no room; then the room of the device they're talking to is used."
)


class AlarmSetTool(_NamedRoomTool):
    name = "alarm_set"
    _description = (
        "Set an alarm. " + _ROOM_ARG + " `time` is "
        "24-hour HH:MM. For a repeating alarm pass `days` as comma-separated "
        "weekday abbreviations (mon,tue,wed,thu,fri,sat,sun). For a one-time "
        "alarm pass `date` as YYYY-MM-DD instead of days (compute it from "
        "'tomorrow', 'this Saturday', etc.). Optionally pass `sound`: a song, "
        "artist, or playlist name to wake up to. The result's `room` says "
        "which room the alarm went to."
    )
    parameters = vol.Schema(
        {
            vol.Required("time"): str,
            vol.Optional("room"): str,
            vol.Optional("days"): str,
            vol.Optional("date"): str,
            vol.Optional("sound"): str,
            vol.Optional("sound_type"): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        args = tool_input.tool_args or {}
        engine, room, error = self._resolve_named(hass, llm_context, args.get("room"))
        if error:
            return {"error": error}
        try:
            alarm = await engine.async_add_alarm(
                time=args["time"],
                room=room,
                days=args.get("days"),
                date=args.get("date"),
                sound=args.get("sound"),
                sound_type=args.get("sound_type"),
            )
        except ValidationError as err:
            return {"error": str(err)}
        return {
            "success": True,
            "room": engine.room_name(room),
            "alarm": engine.alarm_summary(alarm),
        }


class AlarmListTool(_NamedRoomTool):
    name = "alarm_list"
    _description = (
        "List a room's alarms. " + _ROOM_ARG + " Returns each alarm's time, "
        "schedule and whether it's enabled."
    )
    parameters = vol.Schema({vol.Optional("room"): str})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        engine, room, error = self._resolve_named(
            hass, llm_context, (tool_input.tool_args or {}).get("room")
        )
        if error:
            return {"error": error}
        alarms = engine.alarms_for_room(room)
        return {
            "room": engine.room_name(room),
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
        engine, room = self._resolve(hass, llm_context)
        if engine is None or room is None:
            return {"error": "Could not determine which room."}
        if not await engine.async_stop_if_active(room):
            return {"stopped": False, "reason": "no alarm active"}
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
        engine, room = self._resolve(hass, llm_context)
        if engine is None or room is None:
            return {"error": "Could not determine which room."}
        info = await engine.async_snooze(room, (tool_input.tool_args or {}).get("minutes"))
        if info is None:
            return {"snoozed": False, "reason": "no alarm ringing"}
        return {"snoozed": True, "wake_time": info.get("wake_time")}


class AlarmClockAPI(llm.API):
    """LLM API exposing the alarm-clock tools."""

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        prompt = (
            "Alarm-clock tools: alarm_set (create), alarm_list (read) — both "
            "for the user's current room unless they name another — and "
            "alarm_stop (dismiss the ringing alarm), alarm_snooze (silence it "
            "for a few minutes), which act on the user's current room. Do "
            "time/day parsing yourself and pass structured values."
        )
        engine = get_engine(self.hass)
        names = engine.room_choices() if engine else []
        room = engine.resolve_room_for_device(llm_context.device_id) if engine else None
        if engine and room:
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
            tools=[
                AlarmSetTool(names),
                AlarmListTool(names),
                AlarmStopTool(),
                AlarmSnoozeTool(),
            ],
        )
