# Decisions

## General

- CLAUDE.md is tracked and public; house-specific details (hosts, IPs, room records, entity ids, local dashboards, live voice config) live in the untracked CLAUDE.local.md. User-settled, audit 2026-11-15.

## Services and entities

- `set_config` writes the config entry's options (the options form's store) instead of the engine Store, so a reload can't revert it. User-settled, audit 2026-11-15.
- Service handlers that only log a `ValidationError` (room_set with no player shows "Action run successfully") — deferred to backlog item 3. User-settled, audit 2026-11-15.

## Engine

- An alarm's own `sound` never inherits the room's `sound_type`; only an alarm with no sound takes the room's sound and type together. User-settled, audit 2026-11-15.

## Voice

- Named-room matching is case-insensitive on the room's name, then its area's name — no punctuation folding, aliases or room-id matching; the tool descriptions give the model the exact names. User-settled, slice voice-room-name.
- No automated tests for voice/LLM changes; they're verified by the live voice smoke run. User-settled, slice voice-room-name.
- A room without a `name` isn't a case to handle: `async_room_set` always sets one (`setdefault` from the id). Rejected review finding, slice voice-room-name.
