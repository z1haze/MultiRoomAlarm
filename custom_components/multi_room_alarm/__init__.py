"""The Multi-Room Alarm Clock integration.

Layout:
    const.py        constants + the room-record schema (ROOM_KEYS)
    coordinator.py  AlarmClockEngine — persistence, scheduler, fire/ring/snooze
    services.py     the multi_room_alarm.* services (thin engine wrappers)
    llm_api.py      the "Alarm Clock" LLM API (set / list / stop / snooze tools)
    intent.py       sentence-matched intents (stop / snooze / show my alarms)
    binary_sensor.py / sensor.py   per-room ringing / snoozed / next-alarm entities
    config_flow.py  single-instance setup + options

`async_setup` does the once-per-HA-start work; everything per config entry
(one engine, the platforms, the services) lives in `async_setup_entry` so an
options change / room edit can reload the entry cleanly.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CLOCK_ENTITY,
    CONF_WEATHER_ENTITY,
    DATA_ENGINE,
    DOMAIN,
    OPTION_KEYS,
    SENTENCE_TARGET_NAME,
    STATIC_URL_BASE,
    STORAGE_KEY,
    STORAGE_VERSION,
    TONE_FILENAME,
)
from .coordinator import AlarmClockEngine
from .llm_api import async_register_llm_api, async_unregister_llm_api
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# The entity options are always pushed (as "" when unset) so clearing one in the
# UI actually clears it; the numeric/URL options are only pushed when present so
# an empty form doesn't stomp Store defaults.
_ALWAYS_PUSH = (CONF_WEATHER_ENTITY, CONF_CLOCK_ENTITY)


# --------------------------------------------------------------------------- #
# Lifecycle                                                                    #
# --------------------------------------------------------------------------- #
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Once per HA start, before any config entry.

    The static route can't be re-registered and the sentence file only needs
    writing once, so neither can live in async_setup_entry (which re-runs on
    every reload).
    """
    component_dir = Path(__file__).parent
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                f"{STATIC_URL_BASE}/{TONE_FILENAME}",
                str(component_dir / "assets" / TONE_FILENAME),
                False,
            )
        ]
    )
    await hass.async_add_executor_job(_install_sentences, hass, component_dir)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the single config entry: one engine, the platforms, the services."""
    engine = AlarmClockEngine(hass)
    await engine.async_load()

    # Options (set in the UI) win over anything already in the Store.
    opts = {k: entry.options[k] for k in OPTION_KEYS if k in entry.options}
    for k in _ALWAYS_PUSH:
        opts.setdefault(k, "")
    if opts:
        await engine.async_set_config(**opts)

    hass.data.setdefault(DOMAIN, {})[DATA_ENGINE] = engine
    entry.runtime_data = engine

    # Minute tick — the scheduler.
    entry.async_on_unload(async_track_time_change(hass, engine.async_tick, second=0))

    # The LLM API. (Sentence intents register themselves via the intent
    # platform — intent.py::async_setup_intents — nothing to do here.)
    async_register_llm_api(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    async_unregister_llm_api(hass)
    async_unregister_services(hass)
    engine: AlarmClockEngine = entry.runtime_data
    await engine.async_unload()
    hass.data.pop(DOMAIN, None)
    return True


async def _async_reload_on_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> bool:
    """Enable the device page's 'Delete' button. Deleting a room's device
    deletes the room (and its alarms); HA then removes the device + entities."""
    engine: AlarmClockEngine = entry.runtime_data
    for domain, room_id in device.identifiers:
        if domain == DOMAIN and engine.room(room_id) is not None:
            await engine.async_room_delete(room_id)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Wipe the Store and the installed sentence file when the integration is
    removed entirely."""
    await Store(hass, STORAGE_VERSION, STORAGE_KEY).async_remove()
    sentence_file = Path(hass.config.path("custom_sentences", "en", SENTENCE_TARGET_NAME))
    try:
        sentence_file.unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Sentence install                                                             #
# --------------------------------------------------------------------------- #
def _install_sentences(hass: HomeAssistant, component_dir: Path) -> None:
    """Copy assets' `sentences/en.yaml` into <config>/custom_sentences/en/.

    HA's conversation agent only loads custom sentences from that folder, so a
    packaged integration has to place the file there. Rewrites only when the
    content changed; a restart (or conversation reload) activates it.
    """
    src = component_dir / "sentences" / "en.yaml"
    if not src.is_file():
        return
    dst_dir = Path(hass.config.path("custom_sentences", "en"))
    dst = dst_dir / SENTENCE_TARGET_NAME
    try:
        new = src.read_text(encoding="utf-8")
        if dst.is_file() and dst.read_text(encoding="utf-8") == new:
            return
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        _LOGGER.info("Installed alarm sentences to %s (restart HA to activate)", dst)
    except OSError as err:
        _LOGGER.warning("Could not install alarm sentences: %s", err)
