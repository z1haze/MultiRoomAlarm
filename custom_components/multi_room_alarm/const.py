"""Constants for the Multi-Room Alarm Clock integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "multi_room_alarm"

# --------------------------------------------------------------------------- #
# Wiring                                                                       #
# --------------------------------------------------------------------------- #
DATA_ENGINE: Final = "engine"          # hass.data[DOMAIN][DATA_ENGINE]

STORAGE_KEY: Final = DOMAIN            # -> .storage/multi_room_alarm
STORAGE_VERSION: Final = 1

# Bundled alarm tone, served at <ha-url><STATIC_URL_BASE>/<TONE_FILENAME>
STATIC_URL_BASE: Final = "/multi_room_alarm_static"
TONE_FILENAME: Final = "alarm_tone.wav"

# assets/sentences/en.yaml is copied here at setup (a generated artifact).
SENTENCE_TARGET_NAME: Final = "multi_room_alarm.yaml"

# The LLM API tick-box shown in the conversation agent's settings.
LLM_API_ID: Final = "multi_room_alarm"
LLM_API_NAME: Final = "Alarm Clock"

# One dispatcher signal, fired on any ring/snooze/alarm change. A single room's
# entities also listen on f"{SIGNAL_STATE_CHANGED}_{room_id}". Entity add/remove
# is handled by reloading the config entry, not a signal.
SIGNAL_STATE_CHANGED: Final = f"{DOMAIN}_state_changed"

# --------------------------------------------------------------------------- #
# Config-entry options / set_config keys                                       #
# --------------------------------------------------------------------------- #
CONF_SNOOZE_MINUTES: Final = "snooze_minutes"
CONF_DEFAULT_VOLUME: Final = "default_volume"
CONF_TONE_URL: Final = "tone_url"      # override if MASS can't reach HA's auto URL
CONF_WEATHER_ENTITY: Final = "weather_entity"   # dashboard: ringing-screen chip
CONF_CLOCK_ENTITY: Final = "clock_entity"       # dashboard: ringing-screen clock

# Pushed to the engine config on setup + surfaced on sensor.alarms so the
# dashboard cards can read them.
OPTION_KEYS: Final = (
    CONF_SNOOZE_MINUTES,
    CONF_DEFAULT_VOLUME,
    CONF_TONE_URL,
    CONF_WEATHER_ENTITY,
    CONF_CLOCK_ENTITY,
)

# --------------------------------------------------------------------------- #
# Room record — the keys a `room_set` call may store                           #
# --------------------------------------------------------------------------- #
ROOM_KEYS: Final = (
    "name",                 # display name (also the room's HA device name)
    "area",                 # HA area id (or name) — links voice satellites to the room
    "nav_browser",          # browser_mod id — screen navigates here on fire
    "music_player",         # Music Assistant media_player — custom songs play here
    "media_player",         # the device's own media_player — the tone plays here
    "device_volume",        # number.* hardware stream volume (auto-detected)
    "wake_screen_entity",   # switch/button that wakes the display (auto-detected)
    "sound", "sound_type",  # default wake media for the room
    "alarm_volume",         # default volume for the room (0.0-1.0)
    "fire_scene",            # scene.turn_on when an alarm fires
    "fire_on", "fire_off",   # entities -> <domain>.turn_on / turn_off on fire
)

# --------------------------------------------------------------------------- #
# Calendar strings                                                             #
# --------------------------------------------------------------------------- #
WEEKDAYS: Final = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES: Final = {
    "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}
MONTHS_SHORT: Final = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]
MONTHS_LONG: Final = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]

# --------------------------------------------------------------------------- #
# Snooze                                                                       #
# --------------------------------------------------------------------------- #
DEFAULT_SNOOZE_MINUTES: Final = 5
SNOOZE_MIN: Final = 1
SNOOZE_MAX: Final = 60

# --------------------------------------------------------------------------- #
# Ring behaviour                                                               #
# --------------------------------------------------------------------------- #
VALID_SOUND_TYPES: Final = ["artist", "album", "track", "playlist", "radio"]
DEFAULT_ALARM_VOLUME: Final = 0.8

RING_SOUND_SECONDS: Final = 28     # length of alarm_tone.wav (for the timed replay)
RING_REASSERT_SECONDS: Final = 5   # how often the ring loop wakes to check state
RING_REPLAY_MIN_GAP: Final = 20    # never re-issue play_media more often than this
RING_MAX_MINUTES: Final = 15       # safety cap: auto-stop a ring left going this long
SOUND_GRACE_SECONDS: Final = 8     # time a custom song gets to actually start
PLAY_CALL_TIMEOUT: Final = 15      # give up on a wedged play_media call
PLAY_SETTLE_SECONDS: Final = 1     # pause after waking the screen, before playback

# MediaPlayerEntityFeature.REPEAT_SET
FEATURE_REPEAT_SET: Final = 262144
