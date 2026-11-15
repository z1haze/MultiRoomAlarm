# Backlog

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
