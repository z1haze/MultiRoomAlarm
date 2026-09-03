"""Shared base entity for the Multi-Room Alarm Clock."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_STATE_CHANGED
from .coordinator import AlarmClockEngine


def room_signal(room_id: str) -> str:
    return f"{SIGNAL_STATE_CHANGED}_{room_id}"


class AlarmRoomEntity(Entity):
    """Base for entities scoped to one room."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine: AlarmClockEngine, room_id: str) -> None:
        self._engine = engine
        self._room_id = room_id
        room = engine.room(room_id) or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, room_id)},
            name=room.get("name", room_id.replace("_", " ").title()),
            manufacturer="Multi-Room Alarm Clock",
            model="Alarm room",
        )

    @property
    def available(self) -> bool:
        return self._engine.room(self._room_id) is not None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, room_signal(self._room_id), self._handle_update
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_STATE_CHANGED, self._handle_update
            )
        )

    @callback
    def _handle_update(self, *args) -> None:
        self.async_write_ha_state()
