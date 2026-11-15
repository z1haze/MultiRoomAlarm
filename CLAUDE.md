# Home Assistant config repo — working notes

This git repo is scoped version control for **one project** — the multi-room
alarm clock — living inside the live HA config folder. The alarm feature is
built; new work is whatever `docs/agents/backlog.md` lists, plus bug-chasing
and tuning.

---

## Repo / environment constraints (still apply — read before editing)

- The repo is git-init'd **directly inside the live HA config folder**
  (a Samba mount of the HA server's config folder). Files here ARE the live
  files HA reads. No deploy step.
- **This file is tracked and the repo is public.** Anything specific to this
  house (hosts, IPs, room records, entity ids, the local dashboards, the voice
  pipeline's live config) goes in the untracked `CLAUDE.local.md`, never here.
- `.gitignore` is **deny-everything, then allow-list specific paths** (`/*`
  first, then `!/path`). Intentional. Tracked: `custom_components/multi_room_alarm/`,
  `dashboards/alarms/` + `dashboards/example-room.yaml`, `docs/`, `.github/`,
  `README.md`, `LICENSE`, `hacs.json`, `GLOSSARY.md` (when present), this file. Everything else in the
  config folder stays untracked — including the per-room dashboards
  (`dashboards/<room>.yaml`, `dashboards/<room>/`), `lovelace.yaml` and
  `custom_sentences/`. The room dashboards are still audited and edited from
  this checkout (listed in `CLAUDE.local.md`); they just never get
  committed. **Do not broaden it to track the whole config folder** —
  explicitly rejected.
- **Don't let HA's UI rewrite anything this repo tracks.** The dashboards are
  YAML mode on purpose; if scripts/automations ever join the repo, edit the
  YAML directly, not Settings → Scripts / Automations — the UI rewrites the
  whole file in its own format and fights git.
- HA version pinned in `.HA_VERSION`.
- The shipped cards use `button-card`, `stack-in-card`, `layout-card` and
  `card-mod` (all HACS); the local room dashboards add more (see
  `CLAUDE.local.md`).
- Voice/display satellites run **VACA** (View Assist Companion App). The View
  Assist integration itself is **not** installed — VACA is standalone by design.

## Reading live HA state from this checkout — CAVEAT

- There is **no HA API token on the Mac**; HA's REST/WebSocket API is on
  another host. You cannot query live state or the LLM tool list from here.
- **Entity exposure to voice** for anything with an entity-registry entry lives
  in `.storage/core.entity_registry` → each entity's
  `options.conversation.should_expose`. It does **not** live in
  `.storage/homeassistant.exposed_entities` (that file only holds the global
  `expose_new` flag + entries for entities without a registry entry). Check the
  registry, not the exposed_entities store.
- The recorder DB (`home-assistant_v2.db`) is often lock-contended; copy it
  first (`cp home-assistant_v2.db /tmp/...`) then query the copy.
- `.storage/*` comes over Samba; if a file's mtime looks impossibly old,
  suspect client caching and cross-check another store.

---

## The alarm clock — as built

Packaged as a distributable custom integration:
`custom_components/multi_room_alarm/`.

- `coordinator.py` — `AlarmClockEngine`: JSON persistence (HA `Store`,
  key `multi_room_alarm`), minute-tick scheduler, fire / ring / snooze, and the
  in-memory per-room preview (play one alarm's own song without ringing; a ring
  in the room ends it).
- `services.py` — `multi_room_alarm.*` services (thin engine wrappers, except
  `set_config`, which writes the config entry's options).
- `llm_api.py` — the "Alarm Clock" LLM API: tools `alarm_set`, `alarm_list`,
  `alarm_stop`, `alarm_snooze`. Room is resolved server-side from the calling
  device's area; `alarm_set` / `alarm_list` take an optional spoken `room`
  (room name or area name) that overrides it.
- `intent.py` — sentence-matched intents (stop / snooze / show my alarms).
- `binary_sensor.py` / `sensor.py` — per-room `*_alarm_ringing`,
  `*_alarm_snoozed`, `*_next_alarm` entities (all carry a `room` attribute —
  the dashboards match on it plus the id suffix), plus the global `sensor.alarms` (count; full
  list, `previewing` `{room_id: alarm_id}`, and the dashboard's `weather_entity` /
  `clock_entity` as attributes).
- `sentences/en.yaml` — custom voice sentences.
- Dashboards: `dashboards/`. One root (`dashboards/<room>.yaml`) and optional
  folder per room (local-only, not committed; each has its own design) plus
  the shared alarm partials (shipped). `!include` paths are relative to the
  including file's own directory.
  - `lovelace.yaml` — registers each dashboard root; url_path MUST be
    `<roomid>-dashboard` (the alarm partials derive the room from it), and the
    views the integration navigates to must have paths `home` and `alarms`.
  - `dashboards/alarms/` — shared partials: `alarms_row_template.yaml`
    (button-card templates), `alarms_list.yaml` (the alarms subview) wrapping
    `alarms_list_body.yaml` (just the list — a room may wrap it itself),
    `alarms_ringing.yaml` (the ringing overlay, on the Home view).
  - `dashboards/example-room.yaml` — the generic example root that ships.

### Data model (`.storage/multi_room_alarm`)

Four keys: `alarms`, `rooms`, `config`, `snoozes` (pending snoozes — they
survive a restart; ringing state does not).

`alarms[]`: `{ id, time "HH:MM", room, enabled, days[]|date, sound, sound_type,
volume }` — optional overrides: `sound` (with its own `sound_type`) replaces the
room's sound and type together; `volume` replaces the room's volume.
`rooms{}`: keyed by room id, one record per bedroom; the keys are
`const.ROOM_KEYS` (this house's are in `CLAUDE.local.md`).
`config`: `{ snooze_minutes, default_volume, tone_url, weather_entity,
clock_entity }` — copied from the config entry's options on every setup; the
options form and the `set_config` service both write those options.

---

## Voice pipeline

- `prefer_local_intents: true` — HA sentence matching (`sentences/en.yaml`)
  runs first; only unmatched utterances reach the LLM, which gets the "Alarm
  Clock" LLM API tools. The live agent, model and known issues are in
  `CLAUDE.local.md`.
- Authoritative source for any voice bug: Settings → Voice assistants → ⋮ →
  Debug — shows the tool list, the tool-call args, and `stop_reason`.

---

## Descoped / deferred

- Hardware-button snooze (Echo Show volume keys) — on-screen button only.
- Per-alarm (vs per-room) custom fire targets — room lookup table covers it.
- Swipe-to-delete gesture card — not integrated; tap+confirm placeholder.

---

## Agent skills

### Issue tracker

Local markdown under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage labels, recorded as a `Status:` line in each issue file. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
