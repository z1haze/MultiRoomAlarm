"""Config flow for Multi-Room Alarm Clock."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CLOCK_ENTITY,
    CONF_DEFAULT_VOLUME,
    CONF_SNOOZE_MINUTES,
    CONF_TONE_URL,
    CONF_WEATHER_ENTITY,
    DEFAULT_SNOOZE_MINUTES,
    DOMAIN,
)


class MultiRoomAlarmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance setup. Rooms and alarms are managed after setup via
    services (multi_room_alarm.room_set / .add) and voice."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Multi-Room Alarm Clock", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return MultiRoomAlarmOptionsFlow()


class MultiRoomAlarmOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options

        def _suggest(key: str) -> dict | None:
            v = opts.get(key)
            return {"suggested_value": v} if v not in (None, "") else None

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SNOOZE_MINUTES,
                    default=opts.get(CONF_SNOOZE_MINUTES, DEFAULT_SNOOZE_MINUTES),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=60, step=1, mode="box")
                ),
                vol.Optional(
                    CONF_DEFAULT_VOLUME,
                    default=opts.get(CONF_DEFAULT_VOLUME, 0.8),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=1, step=0.05, mode="slider")
                ),
                vol.Optional(
                    CONF_TONE_URL,
                    default=opts.get(CONF_TONE_URL, ""),
                ): selector.TextSelector(),
                # Blank -> the dashboard auto-detects a weather.* entity.
                vol.Optional(
                    CONF_WEATHER_ENTITY, description=_suggest(CONF_WEATHER_ENTITY)
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather")
                ),
                # Blank -> the ringing screen uses the browser clock.
                vol.Optional(
                    CONF_CLOCK_ENTITY, description=_suggest(CONF_CLOCK_ENTITY)
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
