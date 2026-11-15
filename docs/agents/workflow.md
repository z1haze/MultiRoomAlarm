# Workflow

The profile `/slice`, `/idea` and `/audit` read (the agent-kit plugin). Each section heading below is one they refer to by
name; keep the headings.

- **Base branch**: `master`
- **Actors**: sleepers (people waking to an alarm in their bedroom, by voice or the Echo screen); household members
  (setting or checking alarms by voice or dashboard, including from a phone); the admin (configures rooms and installs
  the integration).
- **Packs**: none

## Read

`CLAUDE.md`, `CLAUDE.local.md` (untracked, when present), `README.md`, `GLOSSARY.md` and `docs/adr/` (when present), `custom_components/multi_room_alarm/README.md`.

## Gates

- `python3 -m py_compile custom_components/multi_room_alarm/*.py`
- After push: the GitHub `Validate` workflow (hassfest, HACS) passes on the branch.

## Smoke

- **Live voice check**, when a commit (slice or audit) changes Python under `custom_components/multi_room_alarm/`
  or `sentences/`:
  restart HA, the user runs each voice command in the item's "Done when" list, and the result is checked in
  Settings → Voice assistants → ⋮ → Debug (tool calls, args, response). `/slice` asks the user to run it and report.
- New scenarios go in the spec's acceptance list as spoken commands with their expected result.

## Backlog

- **Where**: `docs/agents/backlog.md`
- **Pick**: the first item whose "Depends on" items are all merged.
- **Item format**: `## N. <title>` followed by **Actor**, **Does**, **Done when** (bullets), **Out of scope** and
  **Depends on**. Detail that doesn't fit (examples, edge cases, open options) goes in
  `.scratch/<feature-slug>/notes.md`.
- **Never on the backlog**: anything already in CLAUDE.md's "Descoped / deferred" list unless the user reopens it;
  changes to HA config outside this project's files (the tracked files plus the local-only room dashboards listed in
  the untracked `CLAUDE.local.md`, which are worked on but never committed).

## Tracker

- **Spec**: `.scratch/<feature-slug>/spec.md`, first line `Branch: slice/<slug>` (see `docs/agents/issue-tracker.md`).
  Find a branch's spec with `grep -l "^Branch: slice/<slug>" .scratch/*/spec.md`.
- **Close**: after the merge, set `Status: resolved` in the spec and remove the item from `docs/agents/backlog.md`.
- **Slices since the last audit**: `git log --merges --oneline <last audit commit>..master | wc -l`, where the
  last audit commit is the latest whose subject starts `audit:`.

## Spec contents

- User Stories separate sleepers, household members and the admin.
- Implementation Decisions name the module (coordinator, llm_api, intent, services, sensors, dashboards) each change
  lands in.
- Assumptions always name the HA version from `.HA_VERSION` and any LLM-model behaviour the change relies on.
- Out of Scope always names anything the change deliberately leaves device-room-only or voice-only.

## Review

- **Max rounds**: 3
- **Pass score**: 9
- **Also required**: none
- **Paid seats**: none

## Docs

- `README.md`: when voice commands, actions/services, entities or dashboard cards change (matching section).
- `custom_components/multi_room_alarm/README.md`: when the integration's user-facing behaviour changes.
- `CLAUDE.md` "The alarm clock — as built": when module responsibilities or the data model change.
- `services.yaml`, `strings.json`, `translations/en.json`: when a service or its fields change.
- `sentences/en.yaml`: when intents change.

## Commits

- **Style**: short lowercase imperative subject, a body only when the reason isn't obvious; no AI attribution or
  `Co-Authored-By` trailers.
- **Audit commit**: subject starts `audit:`.
