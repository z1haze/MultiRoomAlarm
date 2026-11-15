# Decisions

## General

- CLAUDE.md is tracked and public; house-specific details (hosts, IPs, room records, entity ids, local dashboards, live voice config) live in the untracked CLAUDE.local.md. User-settled, audit 2026-11-15.

## Services and entities

- `set_config` writes the config entry's options (the options form's store) instead of the engine Store, so a reload can't revert it. User-settled, audit 2026-11-15.
- Service handlers that only log a `ValidationError` (room_set with no player shows "Action run successfully") — deferred to backlog item 3. User-settled, audit 2026-11-15.

## Engine

- An alarm's own `sound` never inherits the room's `sound_type`; only an alarm with no sound takes the room's sound and type together. User-settled, audit 2026-11-15.
