# Glossary

**Room** — a bedroom the alarm clock serves, with its own alarms, sound, volume and screen. A house has one room per
alarm-clock bedroom, each with an id and a display name. Not every HA area is a room.

**Area** — a Home Assistant area. Each room may point at one area (by id or name); that area's name is also accepted as a
name for the room. An area no room points at is not a room.

**Device's room** — the room whose area holds the device someone is speaking to. A device whose area holds no room (a phone app
has no area) has no device's room, unless the house has only one room.

**Named room** — a room someone says out loud in a command ("…in the guest room"). It overrides the device's room for
setting and listing alarms; stopping and snoozing always act on the device's room.

**Preview** — playing one alarm's own song in its room, on demand, at that alarm's volume, to hear what it will sound
like. Not a ring: no screen wake, no lights or fan, no snooze, no fallback tone. A ring in the same room ends it.
