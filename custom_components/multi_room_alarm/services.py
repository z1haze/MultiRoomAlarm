"""Service registration for the Multi-Room Alarm Clock.

All services are thin wrappers over `AlarmClockEngine`, except `set_config`,
which writes the config entry's options. `add` / `update` answer
with {"ok": bool, ...}, `room_get` / `list` with response data, the rest with
nothing; room services reload the config entry so the platforms rebuild that
room's entities.
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    CONF_DEFAULT_VOLUME,
    CONF_SNOOZE_MINUTES,
    CONF_TONE_URL,
    DATA_ENGINE,
    DOMAIN,
    ROOM_KEYS,
    SNOOZE_MAX,
    SNOOZE_MIN,
)
from .coordinator import AlarmClockEngine, ValidationError

_LOGGER = logging.getLogger(__name__)

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
        # Keys are the options-form keys: set_config writes the entry's options.
        # Same bounds as the options form, which re-validates these as defaults.
        vol.Optional(CONF_SNOOZE_MINUTES): vol.All(
            vol.Coerce(int), vol.Range(min=SNOOZE_MIN, max=SNOOZE_MAX)
        ),
        vol.Optional(CONF_DEFAULT_VOLUME): vol.All(_VOL, vol.Range(min=0, max=1)),
        vol.Optional(CONF_TONE_URL): cv.string,
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

# The schema offers exactly the keys the engine stores (const.ROOM_KEYS) — a
# key added to one side but not the other fails at import, not in the field.
assert {str(k) for k in SCHEMA_ROOM_SET.schema} == {"room_id", *ROOM_KEYS}


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

    # --- preview --------------------------------------------------------- #
    async def preview(call: ServiceCall) -> None:
        try:
            await engine.async_preview(call.data["id"])
        except ValidationError as err:
            raise ServiceValidationError(str(err)) from err

    async def preview_stop(call: ServiceCall) -> None:
        await engine.async_preview_stop(call.data["room"])

    # --- config + rooms ------------------------------------------------- #
    async def set_config(call: ServiceCall) -> None:
        # Saved into the entry's options — the store the options form writes and
        # every setup pushes to the engine — so a later reload can't revert it.
        # The update listener reloads the entry, which hands it to the engine.
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, **call.data}
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

    NONE, OPTIONAL, ONLY = SupportsResponse.NONE, SupportsResponse.OPTIONAL, SupportsResponse.ONLY
    table = (
        ("add", add, SCHEMA_ADD, OPTIONAL),
        ("update", update, SCHEMA_UPDATE, OPTIONAL),
        ("delete", delete, SCHEMA_ID, NONE),
        ("set_enabled", set_enabled, SCHEMA_SET_ENABLED, NONE),
        ("fire", fire, SCHEMA_FIRE, NONE),
        ("stop", stop, SCHEMA_ROOM, NONE),
        ("snooze", snooze, SCHEMA_SNOOZE, NONE),
        ("preview", preview, SCHEMA_ID, NONE),
        ("preview_stop", preview_stop, SCHEMA_ROOM, NONE),
        ("set_config", set_config, SCHEMA_SET_CONFIG, NONE),
        ("room_set", room_set, SCHEMA_ROOM_SET, NONE),
        ("room_get", room_get, SCHEMA_ROOM_GET, ONLY),
        ("room_delete", room_delete, SCHEMA_ROOM_DELETE, NONE),
        ("list", list_, None, ONLY),
    )
    for name, handler, schema, response in table:
        hass.services.async_register(
            DOMAIN, name, handler, schema=schema, supports_response=response
        )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove whatever this domain registered — no second list to keep in step."""
    for service in list(hass.services.async_services_for_domain(DOMAIN)):
        hass.services.async_remove(DOMAIN, service)
