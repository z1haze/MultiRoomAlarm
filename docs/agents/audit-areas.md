# Audit areas

Each area is small enough for one reviewer to read whole. A new path outside every area joins the closest one.

## Engine
`custom_components/multi_room_alarm/coordinator.py`, `const.py`, `__init__.py`, `config_flow.py`, `manifest.json`

## Voice
`custom_components/multi_room_alarm/llm_api.py`, `intent.py`, `sentences/`

## Services and entities
`custom_components/multi_room_alarm/services.py`, `services.yaml`, `strings.json`, `translations/`, `entity.py`,
`sensor.py`, `binary_sensor.py`

## Dashboards
`dashboards/` — the tracked partials (`alarms/`, `example-room.yaml`) plus the **local-only room dashboards**,
which are deliberately gitignored (`/dashboards/*`) but are still audited and worked on from this checkout:
the room dashboards listed in the untracked `CLAUDE.local.md`. Read them whole; they are the real
callers of the integration. Any new `dashboards/<room>.yaml` or `dashboards/<room>/` joins this list.

## Repo and docs
`README.md`, `custom_components/multi_room_alarm/README.md`, `CLAUDE.md`, `.gitignore`, `hacs.json`, `.github/`,
`docs/` (except `docs/agents/`), `LICENSE`, `custom_components/multi_room_alarm/assets/`, `custom_components/multi_room_alarm/brand/`
