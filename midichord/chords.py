"""Chord and scale definitions plus helpers to build chords from a root note."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChordType:
    key: str
    label: str
    intervals: tuple[int, ...]  # semitones above the root
    family: str  # "dyad", "triad" or "tetrad"


_CHORDS = [
    # Dyad
    ChordType("power", "Power chord (5)", (0, 7), "dyad"),
    # Triads
    ChordType("major", "Major", (0, 4, 7), "triad"),
    ChordType("minor", "Minor", (0, 3, 7), "triad"),
    ChordType("dim", "Diminished", (0, 3, 6), "triad"),
    ChordType("aug", "Augmented", (0, 4, 8), "triad"),
    ChordType("sus2", "Suspended 2nd", (0, 2, 7), "triad"),
    ChordType("sus4", "Suspended 4th", (0, 5, 7), "triad"),
    # Tetrads (four-note chords)
    ChordType("maj7", "Major 7th", (0, 4, 7, 11), "tetrad"),
    ChordType("dom7", "Dominant 7th", (0, 4, 7, 10), "tetrad"),
    ChordType("min7", "Minor 7th", (0, 3, 7, 10), "tetrad"),
    ChordType("minmaj7", "Minor-major 7th", (0, 3, 7, 11), "tetrad"),
    ChordType("dim7", "Diminished 7th", (0, 3, 6, 9), "tetrad"),
    ChordType("hdim7", "Half-diminished 7th (m7b5)", (0, 3, 6, 10), "tetrad"),
    ChordType("aug7", "Augmented 7th", (0, 4, 8, 10), "tetrad"),
    ChordType("augmaj7", "Augmented major 7th", (0, 4, 8, 11), "tetrad"),
    ChordType("maj6", "Major 6th", (0, 4, 7, 9), "tetrad"),
    ChordType("min6", "Minor 6th", (0, 3, 7, 9), "tetrad"),
    ChordType("add9", "Add 9", (0, 4, 7, 14), "tetrad"),
    ChordType("7sus4", "Dominant 7th sus4", (0, 5, 7, 10), "tetrad"),
]

CHORD_TYPES: dict[str, ChordType] = {c.key: c for c in _CHORDS}


def chord_keys(*families: str) -> list[str]:
    """Keys of all chord types, optionally limited to the given families."""
    return [c.key for c in _CHORDS if not families or c.family in families]


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Seven-note scales, as semitone offsets from the tonic.
SCALES: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "natural minor": (0, 2, 3, 5, 7, 8, 10),
    "harmonic minor": (0, 2, 3, 5, 7, 8, 11),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
}


def invert(pitches: list[int], inversion: int) -> list[int]:
    """Apply an inversion: the lowest note moves up an octave, `inversion` times.

    The inversion is clamped so that at least one note stays below the others.
    """
    pitches = sorted(pitches)
    inversion = max(0, min(inversion, len(pitches) - 1))
    for _ in range(inversion):
        pitches.append(pitches.pop(0) + 12)
    return sorted(pitches)


def build_chord(root: int, chord_key: str) -> list[int]:
    """Root-position chord of the given type built on `root`."""
    return [root + i for i in CHORD_TYPES[chord_key].intervals]


def build_diatonic_chord(
    note: int, key_root: int, scale: str, size: int = 3
) -> list[int] | None:
    """Stack thirds on `note` using only notes of the scale.

    Returns None when `note` is not in the scale. `size` is 3 (triad) or 4 (seventh chord).
    """
    steps = SCALES[scale]
    pitch_class = (note - key_root) % 12
    if pitch_class not in steps:
        return None
    degree = steps.index(pitch_class)
    base = note - steps[degree]  # tonic in the same octave as `note`
    chord = []
    for k in range(size):
        octave, idx = divmod(degree + 2 * k, len(steps))
        chord.append(base + 12 * octave + steps[idx])
    return chord


_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def parse_key(name: str) -> tuple[int, str] | None:
    """'C major', 'F# minor', 'Bb major' -> (tonic pitch class, scale name in SCALES)."""
    parts = name.strip().split()
    if len(parts) != 2 or parts[1].lower() not in ("major", "minor"):
        return None
    letter, accidentals = parts[0][:1].upper(), parts[0][1:]
    if letter not in _LETTER_PC or any(c not in "#b" for c in accidentals):
        return None
    root = _LETTER_PC[letter] + accidentals.count("#") - accidentals.count("b")
    return root % 12, "major" if parts[1].lower() == "major" else "natural minor"
