"""Service registration for the Multi-Room Alarm Clock.

All services are thin wrappers over `AlarmClockEngine`. Alarm services return a
response dict ({"ok": bool, ...}); room services reload the config entry so the
platforms rebuild that room's entities.
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import CONF_DEFAULT_VOLUME, CONF_SNOOZE_MINUTES, CONF_TONE_URL, DATA_ENGINE, DOMAIN
from .coordinator import AlarmClockEngine, ValidationError

_LOGGER = logging.getLogger(__name__)

SERVICES: tuple[str, ...] = (
    "add", "update", "delete", "set_enabled",
    "fire", "stop", "snooze",
    "set_config", "room_set", "room_get", "room_delete", "list",
)

_VOL = vol.Coerce(float)

# Fields common to `add` and `update`, always optional. `time` / `room` / `id`
# differ in required-ness per service, so they're added explicitly below.
_ALARM_OPTIONAL = {
    vol.Optional("days"): vol.Any(cv.string, [cv.string]),
    vol.Optional("date"): cv.string,
    vol.Optional("enabled"): cv.boolean,
    vol.Optional("sound"): cv.string,
    vol.Optional("sound_type"): cv.string,
    vol.Optional("volume"): _VOL,
}

SCHEMA_ADD = vol.Schema(
    {vol.Required("time"): cv.string, vol.Required("room"): cv.string, **_ALARM_OPTIONAL}
)
SCHEMA_UPDATE = vol.Schema(
    {
        vol.Required("id"): cv.string,
        vol.Optional("time"): cv.string,
        vol.Optional("room"): cv.string,
        **_ALARM_OPTIONAL,
    }
)
SCHEMA_ID = vol.Schema({vol.Required("id"): cv.string})
SCHEMA_SET_ENABLED = vol.Schema({vol.Required("id"): cv.string, vol.Required("enabled"): cv.boolean})
SCHEMA_FIRE = vol.Schema(
    {
        vol.Required("room"): cv.string,
        vol.Optional("alarm_id"): cv.string,
        vol.Optional("sound"): cv.string,
        vol.Optional("sound_type"): cv.string,
        vol.Optional("volume"): _VOL,
    }
)
SCHEMA_ROOM = vol.Schema({vol.Required("room"): cv.string})
SCHEMA_SNOOZE = vol.Schema({vol.Required("room"): cv.string, vol.Optional("snooze"): vol.Coerce(int)})
SCHEMA_SET_CONFIG = vol.Schema(
    {
        vol.Optional("snooze_minutes"): vol.Coerce(int),
        vol.Optional("default_volume"): _VOL,
        vol.Optional("tone_url"): cv.string,
    }
)
SCHEMA_ROOM_SET = vol.Schema(
    {
        vol.Required("room_id"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("area"): cv.string,
        vol.Optional("nav_browser"): cv.string,
        vol.Optional("music_player"): cv.entity_id,
        vol.Optional("media_player"): cv.entity_id,
        vol.Optional("device_volume"): cv.entity_id,
        vol.Optional("wake_screen_entity"): cv.entity_id,
        vol.Optional("sound"): cv.string,
        vol.Optional("sound_type"): cv.string,
        vol.Optional("alarm_volume"): _VOL,
        vol.Optional("fire_scene"): cv.entity_id,
        vol.Optional("fire_on"): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional("fire_off"): vol.All(cv.ensure_list, [cv.entity_id]),
    }
)
SCHEMA_ROOM_DELETE = vol.Schema({vol.Required("room_id"): cv.string})
SCHEMA_ROOM_GET = SCHEMA_ROOM_DELETE  # both take just {room_id}


def async_register_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    engine: AlarmClockEngine = hass.data[DOMAIN][DATA_ENGINE]

    def _reload() -> None:
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    # --- alarms ------------------------------------------------------------ #
    async def add(call: ServiceCall) -> ServiceResponse:
        try:
            alarm = await engine.async_add_alarm(
                time=call.data["time"],
                room=call.data["room"],
                days=call.data.get("days"),
                date=call.data.get("date"),
                enabled=call.data.get("enabled", True),
                sound=call.data.get("sound"),
                sound_type=call.data.get("sound_type"),
                volume=call.data.get("volume"),
            )
        except ValidationError as err:
            return {"ok": False, "error": str(err)}
        return {"ok": True, "id": alarm["id"], "summary": engine.alarm_summary(alarm)}

    async def update(call: ServiceCall) -> ServiceResponse:
        try:
            alarm = await engine.async_update_alarm(
                call.data["id"],
                time=call.data.get("time"),
                room=call.data.get("room"),
                days=call.data.get("days"),
                date=call.data.get("date"),
                enabled=call.data.get("enabled"),
                sound=call.data.get("sound"),
                sound_type=call.data.get("sound_type"),
                volume=call.data.get("volume"),
            )
        except ValidationError as err:
            return {"ok": False, "error": str(err)}
        return {"ok": True, "id": alarm["id"], "summary": engine.alarm_summary(alarm)}

    async def delete(call: ServiceCall) -> None:
        try:
            await engine.async_delete_alarm(call.data["id"])
        except ValidationError as err:
            _LOGGER.warning("delete: %s", err)

    async def set_enabled(call: ServiceCall) -> None:
        try:
            await engine.async_set_enabled(call.data["id"], call.data["enabled"])
        except ValidationError as err:
            _LOGGER.warning("set_enabled: %s", err)

    # --- ring control ---------------------------------------------------- #
    async def fire(call: ServiceCall) -> None:
        await engine.async_fire(
            call.data["room"],
            call.data.get("alarm_id", "manual"),
            call.data.get("sound"),
            call.data.get("sound_type"),
            call.data.get("volume"),
        )

    async def stop(call: ServiceCall) -> None:
        await engine.async_stop(call.data["room"])

    async def snooze(call: ServiceCall) -> None:
        await engine.async_snooze(call.data["room"], call.data.get("snooze"))

    # --- config + rooms ------------------------------------------------- #
    async def set_config(call: ServiceCall) -> None:
        await engine.async_set_config(
            **{
                CONF_SNOOZE_MINUTES: call.data.get("snooze_minutes"),
                CONF_DEFAULT_VOLUME: call.data.get("default_volume"),
                CONF_TONE_URL: call.data.get("tone_url"),
            }
        )

    async def room_set(call: ServiceCall) -> None:
        data = dict(call.data)
        try:
            await engine.async_room_set(data.pop("room_id"), data)
        except ValidationError as err:
            _LOGGER.warning("room_set: %s", err)
            return
        _reload()

    async def room_get(call: ServiceCall) -> ServiceResponse:
        rid = call.data["room_id"]
        config = engine.room(rid)
        return {
            "room_id": rid,
            "found": config is not None,
            # The stored record — round-trips straight back into `room_set`.
            "config": config or {},
            "alarms": engine.alarms_for_room(rid),
        }

    async def room_delete(call: ServiceCall) -> None:
        room_id = call.data["room_id"]
        try:
            await engine.async_room_delete(room_id)
        except ValidationError as err:
            _LOGGER.warning("room_delete: %s", err)
            return
        dev_reg = dr.async_get(hass)
        if device := dev_reg.async_get_device(identifiers={(DOMAIN, room_id)}):
            dev_reg.async_remove_device(device.id)
        _reload()

    async def list_(call: ServiceCall) -> ServiceResponse:
        return {"rooms": engine.rooms, "alarms": engine.alarms}

    register = hass.services.async_register
    OPTIONAL, ONLY = SupportsResponse.OPTIONAL, SupportsResponse.ONLY

    register(DOMAIN, "add", add, schema=SCHEMA_ADD, supports_response=OPTIONAL)
    register(DOMAIN, "update", update, schema=SCHEMA_UPDATE, supports_response=OPTIONAL)
    register(DOMAIN, "delete", delete, schema=SCHEMA_ID)
    register(DOMAIN, "set_enabled", set_enabled, schema=SCHEMA_SET_ENABLED)
    register(DOMAIN, "fire", fire, schema=SCHEMA_FIRE)
    register(DOMAIN, "stop", stop, schema=SCHEMA_ROOM)
    register(DOMAIN, "snooze", snooze, schema=SCHEMA_SNOOZE)
    register(DOMAIN, "set_config", set_config, schema=SCHEMA_SET_CONFIG)
    register(DOMAIN, "room_set", room_set, schema=SCHEMA_ROOM_SET)
    register(DOMAIN, "room_get", room_get, schema=SCHEMA_ROOM_GET, supports_response=ONLY)
    register(DOMAIN, "room_delete", room_delete, schema=SCHEMA_ROOM_DELETE)
    register(DOMAIN, "list", list_, supports_response=ONLY)


def async_unregister_services(hass: HomeAssistant) -> None:
    for service in SERVICES:
        hass.services.async_remove(DOMAIN, service)
