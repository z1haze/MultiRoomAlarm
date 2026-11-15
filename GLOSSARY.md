# Glossary

**Room** — a bedroom the alarm clock serves, with its own alarms, sound, volume and screen. A house has one room per
alarm-clock bedroom, each with an id and a display name. Not every HA area is a room.

**Area** — a Home Assistant area. Each room points at exactly one area; that area's name is also accepted as a
name for the room. An area no room points at is not a room.

**Device's room** — the room whose area holds the device someone is speaking to. A device with no area (a phone app)
has no device's room, unless the house has only one room.

**Named room** — a room someone says out loud in a command ("…in Dad's room"). It overrides the device's room for
setting and listing alarms; stopping and snoozing always act on the device's room.
