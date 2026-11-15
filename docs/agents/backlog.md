# Backlog

## 1. Name the room by voice when setting or listing alarms

**Actor:** someone talking to a device with no room, like the HA app on a phone, or to a device in a different room.
**Does:** `alarm_set` and `alarm_list` (llm_api.py) get an optional `room` argument. The integration matches it, ignoring case, against each room's `name`, its room id and the name of its `area`. If no room is named, it falls back to the calling device's room, as it does now. A room named in the command overrides the device's room.
**Done when:**
- "Set an alarm in the bedroom for 7am" from the phone app creates the alarm for `bedroom`.
- "What alarms are set in the bedroom" from the phone lists them.
- An unknown room name returns an error that lists the valid room names.
- If no room is named and the device has no room, the error asks which room and lists the valid ones.
- The tool descriptions and API prompt stop saying "never ask which room".
**Out of scope:** `alarm_stop` and `alarm_snooze` stay tied to the device's room. The sentence intents in intent.py are unchanged.
**Depends on:** nothing.

## 2. Preview an alarm's music from the alarms list

**Actor:** a household member looking at a room's alarms list.
**Does:** each alarm row whose alarm has its own `sound` (the rows that already show ♪) gets a play button. Tapping it plays that alarm's music on the room's `music_player` at the alarm's volume. Nothing else happens: no ringing overlay, no `fire_on` targets, no snooze. While it plays, that row's button turns into a stop button. Stop ends playback and restores the player's volume. Rows without their own `sound` get no button.
**Done when:**
- Tapping play on a ♪ row plays that alarm's song on the room's `music_player`, and its button shows stop.
- Tapping stop silences it, restores the volume, and the button shows play again.
- Rows without their own `sound` show no button.
- The new action is documented in README Actions and `services.yaml`.
**Out of scope:** previewing the default tone or the room's default music; using preview to test a full ring (that's `fire`).
**Depends on:** nothing. Detail: `.scratch/alarm-preview/notes.md`.

## 3. Audit leftovers (2026-11-15): one way to reach the engine, normalise room ids, surface service errors

**Actor:** the admin, calling `multi_room_alarm.*` from Developer Tools or YAML.
**Does:** closes the round-2 audit nits F16–F20. (a) Platforms and `services.py` read the engine from
`entry.runtime_data`; `hass.data` stays only for `get_engine` (intent / LLM have no entry). (b) Room ids are
normalised (`strip().lower()`) at every entry point — `room()`, `_validate`, `async_room_delete`, `resolve_room_for_device`
— not only in `room_set`, so `room_set room_id: Bedroom` then `add room: Bedroom` works. (c) Every service handler
surfaces a `ValidationError` the same way: raise `ServiceValidationError(str(err))` so Developer Tools shows the
message, instead of `add`/`update` returning `{ok: false}` while `delete`/`set_enabled`/`room_set`/`room_delete` only log.
(d) `snooze` takes `minutes` (keep `snooze` as an alias) so the service, the LLM tool, the intent and the sentences use
one name. (e) `entity.py` uses the stored `name` instead of re-deriving it from the room id.
**Done when:**
- `room_set room_id: Bedroom` followed by `add room: Bedroom` creates the alarm.
- `room_set` with neither player, from Developer Tools, shows the "needs music_player and/or media_player" error.
- `snooze room: bedroom minutes: 7` and `snooze room: bedroom snooze: 7` both snooze for 7 minutes.
- README Actions table and `services.yaml` document `minutes` and drop `{ok, …}` where handlers now raise.
**Out of scope:** changing what the voice paths say; the dashboards.
**Depends on:** nothing.
