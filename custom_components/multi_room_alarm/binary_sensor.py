"""Ringing / snoozed binary sensors, one pair per room."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_ENGINE, DOMAIN
from .coordinator import AlarmClockEngine
from .entity import AlarmRoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    engine: AlarmClockEngine = hass.data[DOMAIN][DATA_ENGINE]
    entities: list[AlarmRoomEntity] = []
    for room_id in engine.room_ids():
        entities.append(AlarmRingingSensor(engine, room_id))
        entities.append(AlarmSnoozedSensor(engine, room_id))
    async_add_entities(entities)


class AlarmRingingSensor(AlarmRoomEntity, BinarySensorEntity):
    _attr_translation_key = "alarm_ringing"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, engine: AlarmClockEngine, room_id: str) -> None:
        super().__init__(engine, room_id)
        self._attr_unique_id = f"{room_id}_alarm_ringing"

    @property
    def is_on(self) -> bool:
        return self._engine.ringing(self._room_id) is not None

    @property
    def extra_state_attributes(self) -> dict:
        info = self._engine.ringing(self._room_id) or {}
        room = self._engine.room(self._room_id) or {}
        return {
            "room": self._room_id,
            "nav_browser": room.get("nav_browser", ""),
            "alarm_id": info.get("alarm_id"),
            "since": info.get("since"),
        }


class AlarmSnoozedSensor(AlarmRoomEntity, BinarySensorEntity):
    _attr_translation_key = "alarm_snoozed"

    def __init__(self, engine: AlarmClockEngine, room_id: str) -> None:
        super().__init__(engine, room_id)
        self._attr_unique_id = f"{room_id}_alarm_snoozed"

    @property
    def is_on(self) -> bool:
        return self._engine.snoozed(self._room_id) is not None

    @property
    def extra_state_attributes(self) -> dict:
        info = self._engine.snoozed(self._room_id) or {}
        return {
            "room": self._room_id,
            "until": info.get("until"),
            "wake_time": info.get("wake_time"),
        }
