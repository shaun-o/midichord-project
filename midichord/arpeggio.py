"""Arpeggio pattern generation."""

from __future__ import annotations

import random

# Pattern key -> label. "random" picks a different pattern for every chord.
PATTERNS: dict[str, str] = {
    "off": "Off (block chords)",
    "up": "Up",
    "down": "Down",
    "updown": "Up then down",
    "downup": "Down then up",
    "shuffle": "Shuffled order",
    "random": "Random pattern per chord",
}

# Step length as a fraction of a quarter note (one beat).
STEP_SIZES: dict[str, float] = {
    "1/4": 1.0,
    "1/8": 0.5,
    "1/8T": 1 / 3,
    "1/16": 0.25,
    "1/16T": 1 / 6,
    "1/32": 0.125,
}

_CONCRETE = ["up", "down", "updown", "downup", "shuffle"]


def pattern_cycle(pitches: list[int], pattern: str, rng: random.Random) -> list[int]:
    """One cycle of the arpeggio, in playing order."""
    ordered = sorted(pitches)
    if pattern == "random":
        pattern = rng.choice(_CONCRETE)
    if pattern == "up":
        return ordered
    if pattern == "down":
        return ordered[::-1]
    if pattern == "updown":
        return ordered + ordered[-2:0:-1]
    if pattern == "downup":
        return ordered[::-1] + ordered[1:-1]
    if pattern == "shuffle":
        shuffled = ordered[:]
        rng.shuffle(shuffled)
        return shuffled
    raise ValueError(f"Unknown arpeggio pattern: {pattern}")


def arpeggiate(
    pitches: list[int],
    start: int,
    end: int,
    ticks_per_beat: int,
    pattern: str,
    step: str,
    gate: float,
    rng: random.Random,
) -> list[tuple[int, int, int]]:
    """Spread `pitches` across [start, end). Returns (pitch, start, end) tuples.

    step == "fit": one cycle of the pattern fills the whole note.
    otherwise:     fixed note value (see STEP_SIZES), cycling until the note ends.
    Returns an empty list if the note is too short to arpeggiate.
    """
    cycle = pattern_cycle(pitches, pattern, rng)
    duration = end - start

    if step == "fit":
        n = len(cycle)
        if duration < n:
            return []
        bounds = [start + duration * i // n for i in range(n + 1)]
        slots = list(zip(bounds[:-1], bounds[1:]))
    else:
        step_ticks = max(1, round(STEP_SIZES[step] * ticks_per_beat))
        slots = []
        t = start
        while t < end:
            slots.append((t, min(t + step_ticks, end)))
            t += step_ticks

    result = []
    for i, (s, e) in enumerate(slots):
        length = min(e - s, max(1, round((e - s) * gate)))
        result.append((cycle[i % len(cycle)], s, s + length))
    return result
