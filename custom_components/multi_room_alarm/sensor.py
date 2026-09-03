"""Next-alarm sensor per room, plus a global alarms-list sensor."""
from __future__ import annotations

import datetime as dt

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLOCK_ENTITY,
    CONF_WEATHER_ENTITY,
    DATA_ENGINE,
    DOMAIN,
    SIGNAL_STATE_CHANGED,
    WEEKDAYS,
)
from .coordinator import AlarmClockEngine, schedule_phrase
from .entity import AlarmRoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    engine: AlarmClockEngine = hass.data[DOMAIN][DATA_ENGINE]
    entities: list[SensorEntity] = [AlarmsListSensor(engine)]
    for room_id in engine.room_ids():
        entities.append(NextAlarmSensor(engine, room_id))
    async_add_entities(entities)


def _next_occurrence(alarm: dict, now: dt.datetime) -> dt.datetime | None:
    try:
        hh, mm = (int(x) for x in alarm["time"].split(":"))
    except (ValueError, KeyError):
        return None
    if alarm.get("date"):
        try:
            y, mo, d = (int(x) for x in alarm["date"].split("-"))
        except ValueError:
            return None
        cand = now.replace(year=y, month=mo, day=d, hour=hh, minute=mm, second=0, microsecond=0)
        return cand if cand >= now else None
    days = alarm.get("days") or []
    for delta in range(0, 8):
        cand = (now + dt.timedelta(days=delta)).replace(
            hour=hh, minute=mm, second=0, microsecond=0
        )
        if WEEKDAYS[cand.weekday()] in days and cand >= now:
            return cand
    return None


class NextAlarmSensor(AlarmRoomEntity, SensorEntity):
    _attr_translation_key = "next_alarm"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, engine: AlarmClockEngine, room_id: str) -> None:
        super().__init__(engine, room_id)
        self._attr_unique_id = f"{room_id}_next_alarm"

    def _compute(self) -> tuple[dt.datetime | None, dict | None]:
        now = dt_util.now()
        best: tuple[dt.datetime, dict] | None = None
        for alarm in self._engine.alarms_for_room(self._room_id):
            if not alarm.get("enabled"):
                continue
            nxt = _next_occurrence(alarm, now)
            if nxt is None:
                continue
            if best is None or nxt < best[0]:
                best = (nxt, alarm)
        return (best[0], best[1]) if best else (None, None)

    @property
    def native_value(self) -> dt.datetime | None:
        return self._compute()[0]

    @property
    def extra_state_attributes(self) -> dict:
        _when, alarm = self._compute()
        return {
            "alarm_id": alarm.get("id") if alarm else None,
            "schedule": schedule_phrase(alarm) if alarm else None,
        }


class AlarmsListSensor(SensorEntity):
    """Integration-wide: alarm count with the full list as an attribute.

    Useful for a dashboard that renders capped alarm slots.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "alarms"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_unique_id = f"{DOMAIN}_alarms"

    def __init__(self, engine: AlarmClockEngine) -> None:
        self._engine = engine

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_STATE_CHANGED, self._updated)
        )

    @callback
    def _updated(self, *args) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return len(self._engine.alarms)

    @property
    def extra_state_attributes(self) -> dict:
        cfg = self._engine.config
        return {
            "alarms": [
                {**a, "schedule": schedule_phrase(a)} for a in self._engine.alarms
            ],
            # Surfaced for the dashboard cards (ringing-screen chip / clock).
            "weather_entity": cfg.get(CONF_WEATHER_ENTITY) or None,
            "clock_entity": cfg.get(CONF_CLOCK_ENTITY) or None,
        }
