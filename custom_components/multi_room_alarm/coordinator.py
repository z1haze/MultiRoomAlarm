"""Core engine for the Multi-Room Alarm Clock.

There are no entity IDs in this file. Everything device-specific is a per-room
record the user supplies (see `const.ROOM_KEYS`); an alarm stores only a `room`
key, and that room's players / lights / screen entity are looked up when it
fires. Adding an alarm-capable room = one `room_set` call, no code change.

Data model — one Store (`.storage/multi_room_alarm`) holding four keys:

    alarms   list of alarm records:
               id       8-hex, generated
               time     "HH:MM" 24h
               room     room_id it belongs to
               enabled  bool
               days     ["mon", ...]      recurring   } exactly
               date     "YYYY-MM-DD"      one-shot    } one of these
               sound (+ its sound_type) / volume     optional overrides
    rooms    {room_id: {name, area, music_player, ...}} — keys in const.ROOM_KEYS
    config   {snooze_minutes, default_volume, tone_url,
              weather_entity, clock_entity}       the last two feed the dashboard
    snoozes  {room_id: {until, alarm_id, sound, ...}} — pending snoozes,
             persisted so they survive a restart

Responsibilities, in the order the sections appear below:
    lifecycle · config · rooms · alarms (CRUD) · published state ·
    voice helpers · firing (fire / ring loop / stop / snooze) · preview ·
    media + volume plumbing · the once-a-minute scheduler · speech formatting
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import uuid
from collections.abc import Iterable
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEFAULT_VOLUME,
    CONF_SNOOZE_MINUTES,
    CONF_TONE_URL,
    DATA_ENGINE,
    DAY_NAMES,
    DEFAULT_ALARM_VOLUME,
    DEFAULT_SNOOZE_MINUTES,
    DOMAIN,
    FEATURE_REPEAT_SET,
    MONTHS_LONG,
    PLAY_CALL_TIMEOUT,
    PLAY_SETTLE_SECONDS,
    PREVIEW_MAX_MINUTES,
    PREVIEW_POLL_SECONDS,
    RING_MAX_MINUTES,
    RING_REASSERT_SECONDS,
    RING_REPLAY_MIN_GAP,
    RING_SOUND_SECONDS,
    ROOM_LIST_KEYS,
    ROOM_STR_KEYS,
    ROOM_VOLUME_KEY,
    SIGNAL_STATE_CHANGED,
    SNOOZE_MAX,
    SNOOZE_MIN,
    SOUND_GRACE_SECONDS,
    STATIC_URL_BASE,
    STORAGE_KEY,
    STORAGE_VERSION,
    TONE_FILENAME,
    VALID_SOUND_TYPES,
    WEEKDAYS,
    dashboard_path,
    room_signal,
)

_LOGGER = logging.getLogger(__name__)

# The two player slots on a room. `music_player` must be a Music Assistant
# entity (custom songs go through `music_assistant.play_media`); `media_player`
# is the device's own player, used for the plain tone via `media_player.play_media`.
_PLAYER_KEYS = ("music_player", "media_player")

# Media-player states that mean "not making sound" — used by the ring loop to
# decide whether playback needs re-kicking.
_DEAD_STATES = ("", "off", "idle", "standby", "unavailable", "unknown", "paused")

# assist_satellite states that mean "not in a voice interaction".
_SATELLITE_IDLE = ("idle", "unavailable", "unknown", "")


class ValidationError(Exception):
    """Raised when an alarm or room record is invalid. The message is shown
    to the user (voice reply / service response / log)."""


# --------------------------------------------------------------------------- #
# Module-level helpers                                                         #
# --------------------------------------------------------------------------- #
def normalize_volume(value: Any) -> float | None:
    """Coerce a volume to a float in [0, 1]. Accepts 0.0-1.0 or a percentage
    (anything > 1 is divided by 100). Returns None if it isn't a number."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v > 1:
        v /= 100.0
    return round(min(max(v, 0.0), 1.0), 3)


def _hhmm_from_iso(iso: str | None) -> str | None:
    """"2026-08-31T06:45:00-07:00" -> "06:45" (None-safe)."""
    if not iso:
        return None
    try:
        return dt.datetime.fromisoformat(iso).strftime("%H:%M")
    except ValueError:
        return None


def schedule_phrase(alarm: dict[str, Any]) -> str:
    """The "when" of an alarm as a phrase — "every day", "on weekdays",
    "on Mondays", "on August 31". Empty string if it has no schedule.
    Used by the sensors, the voice list, and the LLM tool."""
    if alarm.get("date"):
        _y, mo, d = alarm["date"].split("-")
        return f"on {MONTHS_LONG[int(mo) - 1]} {int(d)}"
    days = [d for d in WEEKDAYS if d in (alarm.get("days") or [])]
    if len(days) == 7:
        return "every day"
    if days == ["mon", "tue", "wed", "thu", "fri"]:
        return "on weekdays"
    if days == ["sat", "sun"]:
        return "on weekends"
    names = [DAY_NAMES[d] for d in days]
    if not names:
        return ""
    if len(names) == 1:
        return f"on {names[0]}s"
    return "on " + ", ".join(names[:-1]) + f" and {names[-1]}"


def clock_phrase(alarm: dict[str, Any]) -> str:
    """An alarm's time as speech — "7:30 a.m.". Used by every voice reply."""
    hh, mm = alarm["time"].split(":")
    hh = int(hh)
    return f"{hh % 12 or 12}:{mm} {'a.m.' if hh < 12 else 'p.m.'}"


def get_engine(hass: HomeAssistant) -> AlarmClockEngine | None:
    """The engine, or None before/after the config entry is loaded."""
    return (hass.data.get(DOMAIN) or {}).get(DATA_ENGINE)


class AlarmClockEngine:
    """The whole alarm clock. One instance per config entry, held in
    `hass.data[DOMAIN][DATA_ENGINE]` and on `entry.runtime_data`."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)

        # Persisted, loaded in async_load().
        self._data: dict[str, Any] = {
            "alarms": [],
            "rooms": {},
            "config": {},
            "snoozes": {},
        }

        # Runtime-only state (cleared on restart).
        self._ringing: dict[str, dict[str, Any]] = {}      # room_id -> {alarm_id, since}
        self._snoozed_pub: dict[str, dict[str, Any]] = {}  # room_id -> {until, wake_time}
        self._last_fired: dict[str, str] = {}              # alarm_id -> "YYYY-MM-DDTHH:MM"
        self._prev_volume: dict[str, dict[str, Any]] = {}  # room_id -> {entity: level before ring}
        self._ring_params: dict[str, dict[str, Any]] = {}  # room_id -> resolved sound/volume of current ring
        self._ring_tasks: dict[str, asyncio.Task] = {}     # room_id -> running _ring_loop task
        self._previews: dict[str, dict[str, Any]] = {}     # room_id -> {alarm_id, volume: {entity: level before}}
        self._preview_tasks: dict[str, asyncio.Task] = {}  # room_id -> running _preview_watch task

    # ===================================================================== #
    # Lifecycle                                                              #
    # ===================================================================== #
    async def async_load(self) -> None:
        """Load the Store, purge one-shot alarms whose date has passed, and
        re-publish any snooze that was pending when HA stopped."""
        stored = await self._store.async_load()
        if stored:
            for key in self._data:
                if key in stored:
                    self._data[key] = stored[key]

        today = dt_util.now().strftime("%Y-%m-%d")
        alarms = self._data["alarms"]
        kept = [a for a in alarms if not (a.get("date") and a["date"] < today)]
        if len(kept) != len(alarms):
            _LOGGER.info(
                "Purged %d past one-time alarm(s) missed while HA was off",
                len(alarms) - len(kept),
            )
            self._data["alarms"] = kept

        # A restart kills every ring loop, so nothing is actually ringing.
        # Pending snoozes DO survive a restart (matching the old behaviour) —
        # re-hydrate their published state so the dashboard shows them.
        for room_id, snz in self._data["snoozes"].items():
            self._snoozed_pub[room_id] = {
                "until": snz.get("until"),
                "wake_time": _hhmm_from_iso(snz.get("until")),
            }

        await self._async_save()

    async def async_unload(self) -> None:
        """Cancel every running ring loop and preview watcher."""
        tasks = [*self._ring_tasks.values(), *self._preview_tasks.values()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._ring_tasks.clear()
        self._preview_tasks.clear()

    async def _async_save(self) -> None:
        await self._store.async_save(self._data)

    # ===================================================================== #
    # Config  (snooze · default volume · tone URL · dashboard weather/clock) #
    # ===================================================================== #
    @property
    def config(self) -> dict[str, Any]:
        return self._data["config"]

    async def async_set_config(self, **kwargs: Any) -> None:
        """Merge non-None keyword args into config and persist."""
        self._data["config"].update({k: v for k, v in kwargs.items() if v is not None})
        await self._async_save()

    @property
    def snooze_minutes(self) -> int:
        """Default snooze length, clamped to [SNOOZE_MIN, SNOOZE_MAX]."""
        try:
            n = int(self._data["config"].get(CONF_SNOOZE_MINUTES, DEFAULT_SNOOZE_MINUTES))
        except (TypeError, ValueError):
            n = DEFAULT_SNOOZE_MINUTES
        return min(max(n, SNOOZE_MIN), SNOOZE_MAX)

    @property
    def default_volume(self) -> float:
        """Volume used when neither the alarm nor its room specifies one."""
        return normalize_volume(self._data["config"].get(CONF_DEFAULT_VOLUME)) or DEFAULT_ALARM_VOLUME

    # ===================================================================== #
    # Rooms                                                                  #
    # ===================================================================== #
    @property
    def rooms(self) -> dict[str, dict[str, Any]]:
        return self._data["rooms"]

    def room(self, room_id: str) -> dict[str, Any] | None:
        return self._data["rooms"].get(room_id)

    def room_ids(self) -> list[str]:
        return list(self._data["rooms"].keys())

    async def async_room_set(self, room_id: str, record: dict[str, Any]) -> None:
        """Create or fully replace a room record. Any key not supplied is
        dropped. `device_volume` / `wake_screen_entity` are auto-detected from
        the player's device when omitted; `nav_browser` accepts a device id
        (from the picker) or a raw browser_mod id.

        The caller reloads the config entry afterwards so the platforms rebuild
        this room's entities.
        """
        room_id = room_id.strip().lower()
        if not room_id:
            raise ValidationError("room id is required")

        clean: dict[str, Any] = {}

        # Plain string fields.
        for key in ROOM_STR_KEYS:
            val = record.get(key)
            if isinstance(val, str) and val.strip():
                clean[key] = val.strip()

        # Entity-list fields (the service schema has already made these lists).
        for key in ROOM_LIST_KEYS:
            val = record.get(key)
            if isinstance(val, list) and val:
                clean[key] = val

        vol = normalize_volume(record.get(ROOM_VOLUME_KEY))
        if vol is not None:
            clean[ROOM_VOLUME_KEY] = vol

        if not clean.get("music_player") and not clean.get("media_player"):
            raise ValidationError("a room needs music_player and/or media_player")

        if clean.get("nav_browser"):
            clean["nav_browser"] = self._resolve_browser_id(clean["nav_browser"])
        if not clean.get("device_volume"):
            dv = self._resolve_device_volume(clean.get("media_player"), clean.get("music_player"))
            if dv:
                clean["device_volume"] = dv
        if not clean.get("wake_screen_entity"):
            ws = self._resolve_wake_screen(clean.get("media_player"), clean.get("music_player"))
            if ws:
                clean["wake_screen_entity"] = ws

        clean.setdefault("name", room_id.replace("_", " ").title())

        existed = room_id in self._data["rooms"]
        self._data["rooms"][room_id] = clean
        await self._async_save()
        self._publish_room(room_id)
        _LOGGER.info("Room '%s' %s", room_id, "updated" if existed else "added")

    async def async_room_delete(self, room_id: str) -> None:
        """Remove a room and its alarms from the Store, stopping any ring.

        The device registry is the caller's job — either HA (device deleted in
        the UI) or the room_delete service handler.
        """
        if room_id not in self._data["rooms"]:
            raise ValidationError(f"unknown room '{room_id}'")
        await self._stop(room_id)
        self._data["rooms"].pop(room_id, None)
        self._data["alarms"] = [a for a in self._data["alarms"] if a.get("room") != room_id]
        await self._async_save()
        _LOGGER.info("Room '%s' deleted", room_id)

    # --- room auto-detection ------------------------------------------------ #
    def _device_of(self, entity_id: str | None) -> str | None:
        entry = er.async_get(self.hass).async_get(entity_id) if entity_id else None
        return entry.device_id if entry else None

    def _same_device_entities(self, device_id: str, domain: str) -> list[str]:
        return [
            e.entity_id
            for e in er.async_get(self.hass).entities.values()
            if e.domain == domain and e.device_id == device_id
        ]

    def _resolve_device_volume(self, *players: str | None) -> str | None:
        """Find the `number.*` entity for a player device's hardware stream
        volume (VACA: `<device>_music_volume`). Prefers the most specific
        suffix; skips voice / mic / ducking / notification controls."""
        skip = ("voice", "ducking", "mic", "wake", "ring", "notification", "call")
        for player in players:
            device_id = self._device_of(player)
            if not device_id:
                continue
            numbers = self._same_device_entities(device_id, "number")
            for suffix in ("_music_volume", "_media_volume", "_stream_volume", "_volume"):
                for cand in numbers:
                    oid = cand.split(".", 1)[1]
                    if oid.endswith(suffix) and not any(s in oid for s in skip):
                        return cand
        return None

    def _resolve_wake_screen(self, *players: str | None) -> str | None:
        """Find the `switch.*_screen` that wakes a player device's display.
        Skips screensaver / always-on toggles and the wake-word switch (which
        would start the voice assistant, not wake the screen)."""
        skip = ("screensaver", "always_on", "auto_brightness", "wake_word")
        for player in players:
            device_id = self._device_of(player)
            if not device_id:
                continue
            for cand in self._same_device_entities(device_id, "switch"):
                oid = cand.split(".", 1)[1]
                if oid.endswith("_screen") and not any(s in oid for s in skip):
                    return cand
        return None

    def _resolve_browser_id(self, value: str) -> str:
        """Accept a raw browser_mod id (`browser_mod_xxxx_yyyy`) or a device id
        from the device picker; return the browser id."""
        if value.startswith("browser_mod_"):
            return value
        device = dr.async_get(self.hass).async_get(value)
        if device:
            for domain, ident in device.identifiers:
                if domain == "browser_mod":
                    return ident
        return value

    # ===================================================================== #
    # Alarms — CRUD                                                          #
    # ===================================================================== #
    @property
    def alarms(self) -> list[dict[str, Any]]:
        return self._data["alarms"]

    def get_alarm(self, alarm_id: str) -> dict[str, Any] | None:
        return next((a for a in self._data["alarms"] if a.get("id") == alarm_id), None)

    def alarms_for_room(self, room_id: str) -> list[dict[str, Any]]:
        return sorted(
            (a for a in self._data["alarms"] if a.get("room") == room_id),
            key=lambda a: a.get("time", ""),
        )

    async def async_add_alarm(
        self,
        *,
        time: str,
        room: str,
        days: Any = None,
        date: Any = None,
        enabled: bool = True,
        sound: Any = None,
        sound_type: Any = None,
        volume: Any = None,
    ) -> dict[str, Any]:
        result = self._validate(time, days, room, sound, sound_type, volume, date)
        alarm: dict[str, Any] = {
            "id": uuid.uuid4().hex[:8],
            "time": result["time"],
            "room": result["room"],
            "enabled": bool(enabled),
        }
        if result.get("date"):
            alarm["date"] = result["date"]
        else:
            alarm["days"] = result["days"]
        self._apply_optional(alarm, result)

        self._data["alarms"].append(alarm)
        await self._async_save()
        self._publish_room(room)
        _LOGGER.info("Added alarm %s", alarm)
        return alarm

    async def async_update_alarm(self, alarm_id: str, **changes: Any) -> dict[str, Any]:
        """Edit an alarm in place. Only supplied fields change; passing `days`
        converts a one-shot to recurring and passing `date` does the reverse."""
        target = self.get_alarm(alarm_id)
        if target is None:
            raise ValidationError(f"no alarm with id '{alarm_id}'")

        time = changes.get("time") or target.get("time")
        room = changes.get("room") or target.get("room")
        sound = target.get("sound") if changes.get("sound") is None else changes["sound"]
        stype = target.get("sound_type") if changes.get("sound_type") is None else changes["sound_type"]
        volume = target.get("volume") if changes.get("volume") is None else changes["volume"]

        days, date = changes.get("days"), changes.get("date")
        if days not in (None, "", []):
            new_days, new_date = days, None
        elif isinstance(date, str) and date.strip():
            new_days, new_date = None, date
        else:
            new_days, new_date = target.get("days"), target.get("date")

        result = self._validate(time, new_days, room, sound, stype, volume, new_date)
        target["time"] = result["time"]
        target["room"] = result["room"]
        target.pop("days", None)
        target.pop("date", None)
        if result.get("date"):
            target["date"] = result["date"]
        else:
            target["days"] = result["days"]
        if changes.get("enabled") is not None:
            target["enabled"] = bool(changes["enabled"])
        self._apply_optional(target, result)

        await self._async_save()
        self._publish_room(target["room"])
        _LOGGER.info("Updated alarm %s", target)
        return target

    async def async_delete_alarm(self, alarm_id: str) -> None:
        room = (self.get_alarm(alarm_id) or {}).get("room")
        before = len(self._data["alarms"])
        self._data["alarms"] = [a for a in self._data["alarms"] if a.get("id") != alarm_id]
        if len(self._data["alarms"]) == before:
            raise ValidationError(f"no alarm with id '{alarm_id}'")
        self._last_fired.pop(alarm_id, None)
        await self._async_save()
        if room:
            self._publish_room(room)
        _LOGGER.info("Deleted alarm '%s'", alarm_id)

    async def async_set_enabled(self, alarm_id: str, enabled: bool) -> None:
        target = self.get_alarm(alarm_id)
        if target is None:
            raise ValidationError(f"no alarm with id '{alarm_id}'")
        target["enabled"] = bool(enabled)
        await self._async_save()
        self._publish_room(target["room"])
        _LOGGER.info("Alarm '%s' enabled=%s", alarm_id, bool(enabled))

    # --- validation ------------------------------------------------------- #
    def _validate(
        self,
        time_str: str | None,
        days: Any,
        room: str | None,
        sound: Any = None,
        sound_type: Any = None,
        volume: Any = None,
        date: Any = None,
    ) -> dict[str, Any]:
        """Normalise + check an alarm's fields. Returns a dict with `time`,
        `room`, exactly one of `days`/`date`, and any of `sound`/`sound_type`/
        `volume` that were given. Raises ValidationError (user-facing) on any
        problem."""
        # time -> "HH:MM"
        if not isinstance(time_str, str) or ":" not in time_str:
            raise ValidationError("time is required as 'HH:MM'")
        parts = time_str.split(":")
        try:
            hh, mm = int(parts[0]), int(parts[1])
        except (ValueError, IndexError, TypeError):
            raise ValidationError(f"bad time '{time_str}'")
        if not (0 <= hh <= 23) or not (0 <= mm <= 59):
            raise ValidationError(f"time '{time_str}' is out of range")
        result: dict[str, Any] = {"time": "%02d:%02d" % (hh, mm)}

        # days XOR date
        has_date = isinstance(date, str) and date.strip()
        has_days = days not in (None, "", [])
        if has_date and has_days:
            raise ValidationError("give days OR date, not both")
        if not has_date and not has_days:
            raise ValidationError("days (mon..sun) or date (YYYY-MM-DD) is required")

        if has_date:
            dparts = date.strip().split("-")
            try:
                d = dt.date(int(dparts[0]), int(dparts[1]), int(dparts[2]))
            except (ValueError, IndexError, TypeError):
                raise ValidationError(f"bad date '{date}' (use YYYY-MM-DD)")
            if d < dt_util.now().date():
                raise ValidationError(f"date '{date}' is in the past")
            result["date"] = d.isoformat()
        else:
            if isinstance(days, str):
                days = [x for x in days.lower().replace(" ", ",").split(",") if x]
            if not isinstance(days, list):
                raise ValidationError("days must be a list or comma-separated mon..sun")
            days = [str(x)[:3].lower() for x in days]
            bad = [x for x in days if x not in WEEKDAYS]
            if bad:
                raise ValidationError(f"unknown day(s): {sorted(set(bad))}")
            result["days"] = [x for x in WEEKDAYS if x in days]  # canonical order, deduped
            if not result["days"]:
                raise ValidationError("no valid days given")

        # room
        if not room or room not in self._data["rooms"]:
            raise ValidationError(
                f"unknown room '{room}' (known: {sorted(self._data['rooms'])})"
            )
        result["room"] = room

        # optional sound / sound_type
        if isinstance(sound, str) and sound.strip():
            result["sound"] = sound.strip()
            if isinstance(sound_type, str) and sound_type.strip():
                st = sound_type.strip().lower()
                if st not in VALID_SOUND_TYPES:
                    raise ValidationError(f"sound_type must be one of {VALID_SOUND_TYPES}")
                result["sound_type"] = st

        # optional volume
        if volume not in (None, ""):
            nv = normalize_volume(volume)
            if nv is None:
                raise ValidationError(f"bad volume '{volume}' (use 0.0-1.0 or a percentage)")
            result["volume"] = nv

        return result

    @staticmethod
    def _apply_optional(alarm: dict[str, Any], result: dict[str, Any]) -> None:
        """Copy sound / sound_type / volume from a validated result onto the
        alarm, removing any that are now unset (so `sound: ""` clears it)."""
        for key in ("sound", "sound_type", "volume"):
            if result.get(key) is not None:
                alarm[key] = result[key]
            else:
                alarm.pop(key, None)

    # ===================================================================== #
    # Published state — read by the binary_sensor / sensor entities          #
    # ===================================================================== #
    def ringing(self, room_id: str) -> dict[str, Any] | None:
        """{alarm_id, since} while this room is ringing, else None."""
        return self._ringing.get(room_id)

    def snoozed(self, room_id: str) -> dict[str, Any] | None:
        """{until, wake_time} while this room has a pending snooze, else None."""
        return self._snoozed_pub.get(room_id)

    def previewing(self) -> dict[str, str]:
        """{room_id: alarm_id} for every room playing a preview."""
        return {room_id: p["alarm_id"] for room_id, p in self._previews.items()}

    @callback
    def _publish_room(self, room_id: str) -> None:
        """Tell one room's entities (and the global alarms sensor) to refresh."""
        async_dispatcher_send(self.hass, room_signal(room_id), room_id)
        async_dispatcher_send(self.hass, SIGNAL_STATE_CHANGED)

    @callback
    def _publish_all(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_STATE_CHANGED)
        for room_id in self.room_ids():
            async_dispatcher_send(self.hass, room_signal(room_id), room_id)

    # ===================================================================== #
    # Voice helpers — "which room?" and "is the mic open?"                   #
    # ===================================================================== #
    def resolve_room_for_device(self, device_id: str | None) -> str | None:
        """device -> its area -> the room whose `area` matches (id or name).
        Falls back to the only room if there's exactly one."""
        if device_id:
            device = dr.async_get(self.hass).async_get(device_id)
            if device and device.area_id:
                for room_id in self._data["rooms"]:
                    if self._room_area_id(room_id) == device.area_id:
                        return room_id
        if len(self._data["rooms"]) == 1:
            return next(iter(self._data["rooms"]))
        return None

    def room_name(self, room_id: str) -> str:
        """The room's display name ("Guest Room"), or its id if it has none."""
        return (self.room(room_id) or {}).get("name") or room_id

    def _room_area_entry(self, room_id: str) -> ar.AreaEntry | None:
        """The HA area the room points at. The one place that reads a room's
        `area`, which may be an area id or an area name."""
        ref = str((self.room(room_id) or {}).get("area", "")).strip()
        if not ref:
            return None
        area_reg = ar.async_get(self.hass)
        return area_reg.async_get_area(ref) or area_reg.async_get_area_by_name(ref)

    def _room_area_id(self, room_id: str) -> str | None:
        area = self._room_area_entry(room_id)
        return area.id if area else None

    def room_area_name(self, room_id: str) -> str | None:
        """Name of the HA area the room points at."""
        area = self._room_area_entry(room_id)
        return area.name if area else None

    def room_choices(self) -> list[str]:
        """Each room's name, plus its area's name when that differs —
        "Guest Room (area: Bedroom)" — so the LLM knows both work."""
        out = []
        for room_id in self._data["rooms"]:
            name, area = self.room_name(room_id), self.room_area_name(room_id)
            out.append(f"{name} (area: {area})" if area and area.lower() != name.lower() else name)
        return out

    def resolve_room_by_name(self, spoken: str | None) -> str | None:
        """A spoken room name -> room id. Case-insensitive match on the room's
        `name`, then on the name of the area it points at. Nothing fuzzier:
        the LLM tool descriptions hand the model the exact names."""
        want = str(spoken or "").strip().lower()
        if not want:
            return None
        rooms = self._data["rooms"]
        for room_id in rooms:
            if self.room_name(room_id).lower() == want:
                return room_id
        for room_id in rooms:
            area = self.room_area_name(room_id)
            if area and area.lower() == want:
                return room_id
        return None

    def is_area_listening(self, area_id: str | None) -> bool:
        """True while any assist_satellite in the area is mid voice-interaction.
        The ring loop checks this and stays silent so the mic can hear "stop".
        Computed on demand — no dependency on any ducking integration."""
        if not area_id:
            return False
        return any(
            st.state not in _SATELLITE_IDLE and self._satellite_area(st.entity_id) == area_id
            for st in self.hass.states.async_all("assist_satellite")
        )

    def _satellite_area(self, entity_id: str) -> str | None:
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None:
            return None
        if entry.area_id:
            return entry.area_id
        if entry.device_id:
            device = dr.async_get(self.hass).async_get(entry.device_id)
            if device:
                return device.area_id
        return None

    # ===================================================================== #
    # Firing — fire · ring loop · stop · snooze                             #
    # ===================================================================== #
    async def async_fire(
        self,
        room_id: str,
        alarm_id: str = "manual",
        sound: str | None = None,
        sound_type: str | None = None,
        volume: Any = None,
    ) -> None:
        """Start ringing `room_id`. Called by the `fire` service, the
        scheduler and a snooze that comes due."""
        room = self.room(room_id)
        if not room:
            _LOGGER.error("Fire requested for unknown room '%s'", room_id)
            return

        _LOGGER.warning("FIRING alarm '%s' for room '%s'", alarm_id, room_id)

        # A ring ends a preview in this room. The volume the preview remembered
        # goes to the ring, so stopping the ring restores the pre-preview level.
        preview = await self._preview_end(room_id, restore_volume=False)
        if preview is not None:
            self._prev_volume.setdefault(room_id, preview["volume"])

        # A new alarm fully replaces one already ringing in this room.
        if room_id in self._ringing:
            await self._stop_ring(room_id, self._all_players(room))
        await self._clear_snooze(room_id)

        # Ringing state first — the dashboard overlay keys off this instantly.
        self._ringing[room_id] = {
            "alarm_id": alarm_id,
            "since": dt_util.now().isoformat(timespec="seconds"),
        }
        self._publish_room(room_id)

        # Wake the screen and bring it to the room dashboard so the overlay is
        # visible even if the screen was parked on another view.
        screen = room.get("wake_screen_entity")
        if screen and "." in screen:
            domain = screen.split(".", 1)[0]
            action = "press" if domain == "button" else "turn_on"
            with contextlib.suppress(Exception):
                await self._call(domain, action, entity_id=screen)
        nav = room.get("nav_browser")
        if nav:
            with contextlib.suppress(Exception):
                await self._call(
                    "browser_mod", "navigate",
                    browser_id=nav, path=dashboard_path(room_id, "home"),
                )

        # Give a just-woken device a moment before we touch its media player.
        await asyncio.sleep(PLAY_SETTLE_SECONDS)

        # Sound runs as a background task so the lights below don't delay it.
        # Stash the *resolved* params so a later snooze re-fires exactly this.
        # sound_type belongs to whichever sound is used: an alarm's own song
        # never inherits the room's type (a "playlist" room would misplay it).
        ring_sound = sound or room.get("sound")
        ring_type = sound_type if sound else room.get("sound_type")
        ring_volume = self._target_volume(volume, room)
        self._ring_params[room_id] = {
            "alarm_id": alarm_id,
            "sound": ring_sound,
            "sound_type": ring_type,
            "volume": ring_volume,
        }
        self._ring_tasks[room_id] = self.hass.async_create_background_task(
            self._ring_loop(
                room_id,
                room.get("music_player"),
                room.get("media_player"),
                self._volume_entities(room),
                ring_sound,
                ring_type,
                ring_volume,
            ),
            name=f"{DOMAIN}_ring_{room_id}",
        )

        # Scene / lights / fan — after the ring task is already running.
        await self._run_fire_targets(room)

    async def _ring_loop(
        self,
        room_id: str,
        ma_player: str | None,
        device_player: str | None,
        volume_entities: list[str],
        sound: str | None,
        sound_type: str | None,
        target_vol: float,
    ) -> None:
        """Keep the alarm audible until `self._ringing` loses this room.

        Runs as a background task. Forces `target_vol` on every volume entity
        (remembering the level to restore on stop), then starts playback:
        a custom `sound` via Music Assistant, else the bundled tone on the
        device player. Automatic fallback to the tone if the custom media
        never starts.

        Then, every RING_REASSERT_SECONDS:
          * exit if stopped/snoozed, or auto-stop at RING_MAX_MINUTES
          * stay silent while the room's satellite is listening
          * nudge the volume back up if it drifted down
          * re-issue playback only if the clip ended or the player went dead —
            and never more than once per RING_REPLAY_MIN_GAP, because hammering
            a wedged media stack every few seconds can hang the whole device.
        """
        if not ma_player and not device_player:
            _LOGGER.warning("Room '%s' has no player — alarm is silent", room_id)
            return

        # Capture current levels once, so stop can restore them.
        if room_id not in self._prev_volume:
            self._prev_volume[room_id] = self._vol_snapshot(volume_entities)
        await self._vol_force(volume_entities, target_vol)

        area_id = self._room_area_id(room_id)

        # Initial playback.
        using_tone = not bool(sound)
        active: str | None = None
        if sound:
            active = await self._play_custom(ma_player, sound, sound_type)
            using_tone = active is None
        if using_tone:
            active = await self._play_tone(device_player, ma_player)
        if active is None:
            _LOGGER.warning("Room '%s' — first play failed, retrying in loop", room_id)
            using_tone = True
        await self._set_repeat(active, "one")

        elapsed = 0
        last_play = 0                         # `elapsed` at the last play_media call
        grace_pending = not (using_tone or active is None)  # waiting to confirm a custom song started
        loops = active is not None and self._supports_repeat(active)

        while room_id in self._ringing:
            await asyncio.sleep(SOUND_GRACE_SECONDS if grace_pending else RING_REASSERT_SECONDS)
            elapsed += SOUND_GRACE_SECONDS if grace_pending else RING_REASSERT_SECONDS

            if elapsed >= RING_MAX_MINUTES * 60:
                _LOGGER.warning(
                    "Ring for '%s' hit the %dm cap — auto-stopping", room_id, RING_MAX_MINUTES
                )
                self.hass.async_create_task(self._stop(room_id))
                return

            # Mic is open in this room — go quiet, touch nothing.
            if self.is_area_listening(area_id):
                continue

            for e in volume_entities:
                frac = self._vol_fraction(e)
                if frac is not None and frac < target_vol - 0.05:
                    await self._vol_force(e, target_vol)

            state = self._state(active) if active else None

            # One-time: confirm the custom song actually started.
            if grace_pending:
                grace_pending = False
                if state != "playing":
                    _LOGGER.warning(
                        "Custom sound for '%s' didn't start — using the tone", room_id
                    )
                    using_tone = True
                    active = await self._play_tone(device_player, ma_player) or active
                    await self._set_repeat(active, "one")
                    loops = active is not None and self._supports_repeat(active)
                    last_play = elapsed
                continue

            # Re-kick playback only when genuinely needed, rate-limited.
            # For the tone (known length, may not loop) replay just before
            # it ends; for a custom song rely on the player going idle.
            if elapsed - last_play < RING_REPLAY_MIN_GAP:
                continue
            tone_ended = using_tone and not loops and (elapsed - last_play) >= (RING_SOUND_SECONDS - 2)
            if tone_ended or not state or state in _DEAD_STATES:
                if using_tone or not sound:
                    active = await self._play_tone(device_player, ma_player) or active
                else:
                    active = await self._play_custom(ma_player, sound, sound_type) or active
                await self._set_repeat(active, "one")
                loops = active is not None and self._supports_repeat(active)
                last_play = elapsed

    async def _stop_ring(self, room_id: str, players: Iterable[str]) -> None:
        """Cancel the ring loop task and stop each player. `media_stop` is
        blocking so the state settles to "stopped" before stop/snooze returns —
        otherwise a separate media-ducking integration that paused the player
        during the voice command could race us and un-pause it."""
        task = self._ring_tasks.pop(room_id, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for player in players or []:
            if not player:
                continue
            with contextlib.suppress(Exception):
                await self._call("media_player", "media_stop", entity_id=player, blocking=True)
            await self._set_repeat(player, "off")

    async def async_stop(self, room_id: str) -> None:
        """Public: the `stop` service / overlay button."""
        await self._stop(room_id)

    def is_active(self, room_id: str) -> bool:
        """True while the room is ringing or has a pending snooze."""
        return room_id in self._ringing or room_id in self._snoozed_pub

    async def async_stop_if_active(self, room_id: str) -> bool:
        """The voice paths' stop: False (and nothing touched) if the room is
        neither ringing nor snoozed."""
        if not self.is_active(room_id):
            return False
        await self._stop(room_id)
        return True

    async def _stop(self, room_id: str) -> None:
        await self._teardown(room_id)
        self._ring_params.pop(room_id, None)
        await self._clear_snooze(room_id)
        _LOGGER.info("Stopped alarm for room '%s'", room_id)

    async def _teardown(self, room_id: str) -> None:
        """Silence a room and restore its volume. Shared by stop and snooze;
        does NOT clear the snooze record (snooze writes it right after)."""
        room = self.room(room_id) or {}
        self._ringing.pop(room_id, None)
        await self._stop_ring(room_id, self._all_players(room))
        await self._vol_restore_all(self._prev_volume.pop(room_id, {}))
        self._publish_room(room_id)

    async def async_snooze(self, room_id: str, minutes: Any = None) -> dict[str, Any] | None:
        """Silence now, re-fire after N minutes (the minute tick handles the
        re-fire). Returns {until, wake_time} or None if nothing was ringing."""
        if room_id not in self._ringing:
            _LOGGER.debug("Snooze for '%s' but nothing is ringing", room_id)
            return None

        try:
            mins = min(max(int(minutes), SNOOZE_MIN), SNOOZE_MAX)
        except (TypeError, ValueError):
            mins = self.snooze_minutes

        # Carry the exact params of the ring that's playing (fall back to the
        # alarm record if we somehow have none).
        params = self._ring_params.get(room_id)
        if not params:
            alarm = self.get_alarm(self._ringing[room_id]["alarm_id"]) or {}
            params = {
                "alarm_id": self._ringing[room_id]["alarm_id"],
                "sound": alarm.get("sound"),
                "sound_type": alarm.get("sound_type"),
                "volume": alarm.get("volume"),
            }

        # Round "now" to the nearest minute, then add the snooze length, so a
        # 5-minute snooze fires ~5 min later on a clean minute, not 5-6.
        now = dt_util.now()
        base = now.replace(second=0, microsecond=0)
        if now.second >= 30:
            base += dt.timedelta(minutes=1)
        until = base + dt.timedelta(minutes=mins)

        self._data["snoozes"][room_id] = {
            "until": until.isoformat(timespec="seconds"),
            "alarm_id": params.get("alarm_id"),
            "sound": params.get("sound"),
            "sound_type": params.get("sound_type"),
            "volume": params.get("volume"),
        }
        await self._async_save()
        await self._teardown(room_id)

        self._snoozed_pub[room_id] = {
            "until": until.isoformat(timespec="seconds"),
            "wake_time": until.strftime("%H:%M"),
        }
        self._publish_room(room_id)
        _LOGGER.info("Snoozed '%s' for %d min (wake %s)", room_id, mins, until.strftime("%H:%M"))
        return self._snoozed_pub[room_id]

    async def _clear_snooze(self, room_id: str) -> None:
        if self._data["snoozes"].pop(room_id, None) is not None:
            await self._async_save()
        self._snoozed_pub.pop(room_id, None)
        self._publish_room(room_id)

    # ===================================================================== #
    # Preview — hear an alarm's own song, without ringing                  #
    # ===================================================================== #
    async def async_preview(self, alarm_id: str) -> None:
        """Play one alarm's own song on its room's music player at the alarm's
        volume. No screen wake, no fire targets, no repeat, no tone fallback.
        A preview already playing in the room is replaced; the volume it
        remembered is kept, so the eventual stop restores the original level."""
        alarm = self.get_alarm(alarm_id)
        if alarm is None:
            raise ValidationError(f"no alarm with id '{alarm_id}'")
        if not alarm.get("sound"):
            raise ValidationError("that alarm has no song of its own to preview")
        room_id = alarm.get("room")
        room = self.room(room_id) or {}
        if room_id in self._ringing:
            raise ValidationError(f"room '{room_id}' is ringing")
        player = room.get("music_player")
        if not player:
            raise ValidationError(f"room '{room_id}' has no music_player to preview on")

        old = await self._preview_end(room_id, restore_volume=False)
        entities = self._volume_entities(room)
        record = {
            "alarm_id": alarm_id,
            "volume": old["volume"] if old else self._vol_snapshot(entities),
        }
        self._previews[room_id] = record
        self._publish_room(room_id)

        await self._vol_force(entities, self._target_volume(alarm.get("volume"), room))
        active = await self._play_custom(player, alarm["sound"], alarm.get("sound_type"))
        await self._set_repeat(active, "off")

        # A ring, a stop or another preview may have taken over while we played.
        # Stopped (nothing else owns the room now): silence what we just started.
        if self._previews.get(room_id) is not record:
            if active and room_id not in self._previews and room_id not in self._ringing:
                with contextlib.suppress(Exception):
                    await self._call("media_player", "media_stop", entity_id=active, blocking=True)
            return
        if active is None:
            await self._preview_end(room_id)
            return
        self._preview_tasks[room_id] = self.hass.async_create_background_task(
            self._preview_watch(room_id, player), name=f"{DOMAIN}_preview_{room_id}"
        )
        _LOGGER.info("Previewing alarm '%s' in room '%s'", alarm_id, room_id)

    async def async_preview_stop(self, room_id: str) -> None:
        """Public: the `preview_stop` service. No-op if the room isn't previewing."""
        await self._preview_end(room_id)

    async def _preview_watch(self, room_id: str, player: str) -> None:
        """End the preview when the song didn't start within the grace period,
        when the player stops playing, or at PREVIEW_MAX_MINUTES."""
        await asyncio.sleep(SOUND_GRACE_SECONDS)
        elapsed = SOUND_GRACE_SECONDS
        if self._state(player) == "playing":
            while elapsed < PREVIEW_MAX_MINUTES * 60:
                await asyncio.sleep(PREVIEW_POLL_SECONDS)
                elapsed += PREVIEW_POLL_SECONDS
                if self._state(player) not in ("playing", "buffering"):
                    break
        else:
            _LOGGER.warning("Preview song for '%s' didn't start — ending the preview", room_id)
        await self._preview_end(room_id)

    async def _preview_end(
        self, room_id: str, *, restore_volume: bool = True
    ) -> dict[str, Any] | None:
        """Stop the room's preview: cancel its watcher (unless that's the
        caller), stop the music player and, unless the caller takes the
        remembered volume over, restore it. Returns the ended preview or None."""
        preview = self._previews.pop(room_id, None)
        if preview is None:
            return None
        task = self._preview_tasks.pop(room_id, None)
        if task and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        player = (self.room(room_id) or {}).get("music_player")
        if player:
            with contextlib.suppress(Exception):
                await self._call("media_player", "media_stop", entity_id=player, blocking=True)
        if restore_volume:
            await self._vol_restore_all(preview["volume"])
        self._publish_room(room_id)
        _LOGGER.info("Preview ended in room '%s'", room_id)
        return preview

    # ===================================================================== #
    # Media + volume plumbing                                               #
    # ===================================================================== #
    def _state(self, entity_id: str | None) -> str | None:
        st = self.hass.states.get(entity_id) if entity_id else None
        return st.state if st else None

    def _attr(self, entity_id: str | None, attr: str, default: Any = None) -> Any:
        st = self.hass.states.get(entity_id) if entity_id else None
        return st.attributes.get(attr, default) if st else default

    async def _call(self, domain: str, service: str, blocking: bool = False, **data: Any) -> None:
        """`hass.services.async_call` that logs and re-raises on failure."""
        try:
            await self.hass.services.async_call(domain, service, data, blocking=blocking)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("%s.%s %s failed: %s", domain, service, data, err)
            raise

    # --- players --------------------------------------------------------- #
    def _all_players(self, room: dict[str, Any]) -> list[str]:
        """Both configured players, de-duplicated."""
        out: list[str] = []
        for key in _PLAYER_KEYS:
            p = room.get(key)
            if p and p not in out:
                out.append(p)
        return out

    def _volume_entities(self, room: dict[str, Any]) -> list[str]:
        """Everything the alarm forces volume on: both players (software level)
        plus the device_volume number (the hardware stream level, which is what
        actually makes it loud)."""
        out = self._all_players(room)
        dv = room.get("device_volume")
        if dv and dv not in out:
            out.append(dv)
        return out

    def _supports_repeat(self, player: str | None) -> bool:
        try:
            return bool(int(self._attr(player, "supported_features", 0) or 0) & FEATURE_REPEAT_SET)
        except (TypeError, ValueError):
            return False

    async def _set_repeat(self, player: str | None, mode: str) -> None:
        """Loop the current track if the player supports it; no-op otherwise
        (the ring loop's timed replay covers non-looping players)."""
        if not player or not self._supports_repeat(player):
            return
        with contextlib.suppress(Exception):
            await self._call("media_player", "repeat_set", entity_id=player, repeat=mode)

    # --- volume --------------------------------------------------------- #
    # A "volume entity" is either a media_player (volume_level, 0.0-1.0) or a
    # number.* whose value we scale onto its own min/max/step.
    @staticmethod
    def _is_number(entity: str) -> bool:
        return entity.split(".", 1)[0] == "number"

    def _vol_get(self, entity: str | None) -> float | None:
        """Current volume as a raw value: 0.0-1.0 for a media_player, the
        native number for a `number.*`. None if unknown."""
        if not entity:
            return None
        if self._is_number(entity):
            try:
                return float(self._state(entity))
            except (TypeError, ValueError):
                return None
        return self._attr(entity, "volume_level")

    def _vol_fraction(self, entity: str | None) -> float | None:
        """Current volume normalised to 0.0-1.0 (for drift checks)."""
        raw = self._vol_get(entity)
        if raw is None:
            return None
        if self._is_number(entity):
            lo = float(self._attr(entity, "min", 0) or 0)
            hi = float(self._attr(entity, "max", 100) or 100)
            return (raw - lo) / (hi - lo) if hi > lo else None
        return raw

    async def _vol_force(self, entities: Iterable[str] | str, fraction: float | None) -> None:
        """Set every entity to `fraction` (0.0-1.0). Best-effort per entity."""
        if fraction is None:
            return
        if isinstance(entities, str):
            entities = [entities]
        for e in entities:
            if not e:
                continue
            with contextlib.suppress(Exception):
                if self._is_number(e):
                    lo = float(self._attr(e, "min", 0) or 0)
                    hi = float(self._attr(e, "max", 100) or 100)
                    step = float(self._attr(e, "step", 1) or 1)
                    val = min(max(round((lo + fraction * (hi - lo)) / step) * step, lo), hi)
                    await self._call("number", "set_value", entity_id=e, value=val)
                else:
                    await self._call("media_player", "volume_set", entity_id=e, volume_level=fraction)

    async def _vol_restore(self, entity: str, raw: Any) -> None:
        """Restore one entity to a raw value captured by _vol_get()."""
        if not entity or raw is None:
            return
        with contextlib.suppress(Exception):
            if self._is_number(entity):
                await self._call("number", "set_value", entity_id=entity, value=raw)
            else:
                await self._call("media_player", "volume_set", entity_id=entity, volume_level=raw)

    def _vol_snapshot(self, entities: Iterable[str]) -> dict[str, Any]:
        """Current raw level of each entity, for _vol_restore_all() later."""
        return {e: raw for e in entities if (raw := self._vol_get(e)) is not None}

    async def _vol_restore_all(self, snapshot: dict[str, Any]) -> None:
        for entity, raw in snapshot.items():
            await self._vol_restore(entity, raw)

    def _target_volume(self, volume: Any, room: dict[str, Any]) -> float:
        """The level a ring or preview plays at: the alarm's own volume, else
        the room's `alarm_volume`, else the default volume."""
        v = normalize_volume(volume if volume is not None else room.get(ROOM_VOLUME_KEY))
        return self.default_volume if v is None else v

    # --- what to play --------------------------------------------------- #
    def _tone_url(self) -> str:
        """URL of the bundled alarm tone. `tone_url` config overrides it (set
        that if Music Assistant can't reach HA's auto-detected URL)."""
        override = self._data["config"].get(CONF_TONE_URL)
        if override:
            return override
        try:
            base = get_url(self.hass, allow_external=False, prefer_external=False)
        except NoURLAvailableError:
            try:
                base = get_url(self.hass)
            except NoURLAvailableError:
                base = ""
        return f"{base}{STATIC_URL_BASE}/{TONE_FILENAME}"

    async def _play_tone(self, device_player: str | None, ma_player: str | None) -> str | None:
        """Play the bundled tone — directly on the device player (shortest,
        most reliable path), falling back to Music Assistant. Returns the
        entity it's playing on, or None. Each call is time-boxed."""
        url = self._tone_url()
        attempts = [
            (device_player, "media_player", "play_media",
             {"media_content_id": url, "media_content_type": "music"}),
            (ma_player, "music_assistant", "play_media",
             {"media_id": url, "enqueue": "replace"}),
        ]
        for player, domain, service, extra in attempts:
            if not player:
                continue
            try:
                async with asyncio.timeout(PLAY_CALL_TIMEOUT):
                    await self._call(domain, service, blocking=True, entity_id=player, **extra)
                return player
            except (Exception, TimeoutError):  # noqa: BLE001
                continue
        return None

    async def _play_custom(
        self, ma_player: str | None, sound: str | None, sound_type: str | None
    ) -> str | None:
        """Play custom media through Music Assistant. Returns the MA entity on
        success, None on failure (caller falls back to the tone)."""
        if not ma_player or not sound:
            return None
        data: dict[str, Any] = {"entity_id": ma_player, "media_id": sound, "enqueue": "replace"}
        if sound_type:
            data["media_type"] = sound_type
        try:
            async with asyncio.timeout(PLAY_CALL_TIMEOUT):
                await self._call("music_assistant", "play_media", blocking=True, **data)
            return ma_player
        except (Exception, TimeoutError):  # noqa: BLE001
            _LOGGER.warning("custom media '%s' on %s failed", sound, ma_player)
            return None

    async def _run_fire_targets(self, room: dict[str, Any]) -> None:
        """Run a room's fire_scene / fire_on / fire_off (order: scene, on, off).
        Best-effort; one bad entity never aborts the rest. Stop/snooze do NOT
        reverse any of this — if you're up, you want the lights to stay."""
        scene = room.get("fire_scene")
        if scene:
            with contextlib.suppress(Exception):
                await self._call("scene", "turn_on", entity_id=scene)
        for action, key in (("turn_on", "fire_on"), ("turn_off", "fire_off")):
            for entity_id in room.get(key, []):
                if "." not in entity_id:
                    continue
                with contextlib.suppress(Exception):
                    await self._call(entity_id.split(".", 1)[0], action, entity_id=entity_id)

    # ===================================================================== #
    # Scheduler — one tick at the top of every wall-clock minute             #
    # ===================================================================== #
    @callback
    def async_tick(self, now: dt.datetime | None = None) -> None:
        """`async_track_time_change` callback — hands off to the async worker."""
        self.hass.async_create_task(self._async_tick(now))

    async def _async_tick(self, now: dt.datetime | None = None) -> None:
        now = dt_util.now() if now is None else dt_util.as_local(now)
        weekday = WEEKDAYS[now.weekday()]
        today = now.strftime("%Y-%m-%d")
        hm = now.strftime("%H:%M")
        stamp = now.strftime("%Y-%m-%dT%H:%M")

        # 1. Due snoozes re-fire the same alarm.
        due: list[str] = []
        for room_id, snz in list(self._data["snoozes"].items()):
            try:
                until = dt.datetime.fromisoformat(snz.get("until", ""))
            except ValueError:
                due.append(room_id)  # unparseable -> drop it
                continue
            if now.replace(tzinfo=None) >= until.replace(tzinfo=None):
                due.append(room_id)
        for room_id in due:
            snz = self._data["snoozes"].pop(room_id, {})
            self._snoozed_pub.pop(room_id, None)
            self._publish_room(room_id)
            _LOGGER.info("Snooze for '%s' is up — re-firing", room_id)
            await self.async_fire(
                room_id, snz.get("alarm_id", "snooze"),
                snz.get("sound"), snz.get("sound_type"), snz.get("volume"),
            )
        if due:
            await self._async_save()

        # 2. Scheduled alarms — recurring (`days`) or one-shot (`date`).
        expired: list[str] = []
        for alarm in list(self._data["alarms"]):
            aid = alarm.get("id", "?")

            # Sweep stale one-shots — disabled, or enabled but missed because HA
            # was down over the fire minute. Otherwise they'd sit on the
            # dashboard as a past-dated row until the next restart's purge.
            if alarm.get("date") and alarm["date"] < today:
                expired.append(aid)
                continue

            if not alarm.get("enabled") or alarm.get("time") != hm:
                continue
            if alarm.get("date"):
                if alarm["date"] != today:
                    continue
            elif weekday not in alarm.get("days", []):
                continue

            if self._last_fired.get(aid) == stamp:  # already fired this minute
                continue
            self._last_fired[aid] = stamp
            await self.async_fire(
                alarm.get("room"), aid,
                alarm.get("sound"), alarm.get("sound_type"), alarm.get("volume"),
            )
            if alarm.get("date"):  # one-shot: delete after firing
                expired.append(aid)

        if expired:
            self._data["alarms"] = [a for a in self._data["alarms"] if a.get("id") not in expired]
            for aid in expired:
                self._last_fired.pop(aid, None)
            await self._async_save()
            self._publish_all()

    # ===================================================================== #
    # Speech formatting (voice confirmations)                               #
    # ===================================================================== #
    @staticmethod
    def alarm_summary(alarm: dict[str, Any]) -> str:
        """"7:30 a.m. on weekdays" — the add/update confirmation (service
        response + alarm_set tool). Same words the list paths speak."""
        return f"{clock_phrase(alarm)} {schedule_phrase(alarm)}".strip()
