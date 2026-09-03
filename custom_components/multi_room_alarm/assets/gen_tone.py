"""Generate the alarm ring tone at ../../www/alarm/alarm_tone.wav.

Pure standard library, no deps. Re-run after tweaking the knobs below:

    python3 pyscript/alarm_data/gen_tone.py

If you change the length, update RING_SOUND_SECONDS in alarm_clock.py.
"""
import math
import os
import struct
import wave

RATE = 44100
BITS = 16
AMP = 0.62  # headroom so it's loud but not clipping

# One "cycle" = 4 alternating beeps then a rest. Repeat CYCLES times.
BEEP_MS = 170
GAP_MS = 110
REST_MS = 780
FREQS = [2093.0, 1760.0, 2093.0, 1760.0]   # C7 / A6 alternating — urgent
CYCLES = 16

ATTACK_MS = 6
RELEASE_MS = 10

CYCLE_MS = len(FREQS) * (BEEP_MS + GAP_MS) - GAP_MS + REST_MS
TOTAL_MS = CYCLE_MS * CYCLES

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "www", "alarm", "alarm_tone.wav")


def env(i, n):
    a = int(RATE * ATTACK_MS / 1000)
    r = int(RATE * RELEASE_MS / 1000)
    if i < a:
        return i / a
    if i > n - r:
        return max(0.0, (n - i) / r)
    return 1.0


def main():
    samples = []
    total = int(RATE * TOTAL_MS / 1000)
    beep_n = int(RATE * BEEP_MS / 1000)
    gap_n = int(RATE * GAP_MS / 1000)
    rest_n = int(RATE * REST_MS / 1000)

    while len(samples) < total:
        for idx, f in enumerate(FREQS):
            for i in range(beep_n):
                samples.append(AMP * env(i, beep_n) * math.sin(2 * math.pi * f * i / RATE))
            samples.extend([0.0] * (rest_n if idx == len(FREQS) - 1 else gap_n))

    samples = samples[:total]
    for k in range(min(64, len(samples))):  # exact silence at the seam -> click-free loop
        samples[-1 - k] *= k / 64.0

    path = os.path.normpath(OUT)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(BITS // 8)
        w.setframerate(RATE)
        w.writeframes(b"".join(
            struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples
        ))
    print(f"wrote {path}: {TOTAL_MS / 1000:.2f}s ({CYCLE_MS / 1000:.2f}s cycle x {CYCLES})")


if __name__ == "__main__":
    main()
