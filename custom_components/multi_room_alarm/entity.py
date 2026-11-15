"""Shared base entity for the Multi-Room Alarm Clock."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_STATE_CHANGED, room_signal
from .coordinator import AlarmClockEngine


class AlarmBaseEntity(Entity):
    """Base for every alarm entity: pushed, not polled — rewrites its state
    whenever the engine fires one of `_signals()`."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, engine: AlarmClockEngine) -> None:
        self._engine = engine

    def _signals(self) -> tuple[str, ...]:
        return (SIGNAL_STATE_CHANGED,)

    async def async_added_to_hass(self) -> None:
        for signal in self._signals():
            self.async_on_remove(
                async_dispatcher_connect(self.hass, signal, self._handle_update)
            )

    @callback
    def _handle_update(self, *args) -> None:
        self.async_write_ha_state()


class AlarmRoomEntity(AlarmBaseEntity):
    """Base for entities scoped to one room: on that room's device, and
    also refreshed by the room's own signal."""

    def __init__(self, engine: AlarmClockEngine, room_id: str) -> None:
        super().__init__(engine)
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

    def _signals(self) -> tuple[str, ...]:
        return (room_signal(self._room_id), SIGNAL_STATE_CHANGED)
