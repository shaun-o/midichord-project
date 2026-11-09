"""Summarise a MIDI file: tempo, time signature, key, channels and instruments."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import mido

from .chords import NOTE_NAMES, parse_key
from .transform import NoteEvent, extract_notes, isolated_indices

GM_PROGRAMS = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet",
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)",
    "Electric Guitar (clean)", "Electric Guitar (muted)", "Overdriven Guitar",
    "Distortion Guitar", "Guitar Harmonics",
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2",
    "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute",
    "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)",
    "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass+lead)",
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]

# Krumhansl-Kessler key profiles (index 0 = tonic).
_MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


@dataclass
class ChannelInfo:
    channel: int  # 0-based
    notes: int
    low: int
    high: int
    program: int | None
    single: int = 0  # notes that do not overlap any other note on the channel

    @property
    def instrument(self) -> str:
        if self.channel == 9:
            return "Drums / percussion"
        if self.program is not None and 0 <= self.program < 128:
            return GM_PROGRAMS[self.program]
        return "-"


@dataclass
class FileInfo:
    midi_type: int
    track_count: int
    ticks_per_beat: int
    length_seconds: float | None
    title: str | None
    tempos: list[tuple[int, float]]  # (tick, bpm), sorted; may be a default
    tempo_declared: bool
    time_signatures: list[str]
    declared_keys: list[str]
    estimated_key: str | None
    estimated_score: float
    channels: list[ChannelInfo] = field(default_factory=list)

    @property
    def suggested_key(self) -> tuple[int, str] | None:
        """(tonic, scale) from the declared key signature, else from the estimate."""
        for name in [*self.declared_keys, self.estimated_key]:
            if name and (parsed := parse_key(name)):
                return parsed
        return None

    @property
    def suggested_key_name(self) -> str | None:
        for name in [*self.declared_keys, self.estimated_key]:
            if name and parse_key(name):
                return name
        return None

    @property
    def total_notes(self) -> int:
        return sum(c.notes for c in self.channels)


# ---------------------------------------------------------------------- formatting


def note_name(n: int) -> str:
    """MIDI note number to name, with middle C (60) = C4."""
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def format_bpm(bpm: float) -> str:
    text = f"{bpm:.1f}"
    return text[:-2] if text.endswith(".0") else text


def format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


def tempo_text(info: FileInfo) -> str:
    bpms = [b for _, b in info.tempos]
    if not info.tempo_declared:
        return "120 BPM (default - none in file)"
    if len(bpms) == 1:
        return f"{format_bpm(bpms[0])} BPM"
    return (f"{format_bpm(min(bpms))}-{format_bpm(max(bpms))} BPM "
            f"(varies, starts at {format_bpm(bpms[0])})")


# ------------------------------------------------------------------------ key


def _key_name(key: str) -> str:
    """mido key names like 'C', 'Am', 'F#m' to 'C major', 'A minor', 'F# minor'."""
    if key.endswith("m"):
        return f"{key[:-1]} minor"
    return f"{key} major"


def _pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else 0.0


def estimate_key(notes: list[NoteEvent]) -> tuple[str | None, float]:
    """Estimate the key with the Krumhansl-Schmuckler algorithm.

    Weights each pitch class by total sounding time; drums (channel 10) are ignored.
    Returns (name, correlation) or (None, 0) if there is too little material.
    """
    histogram = [0.0] * 12
    for n in notes:
        if n.channel != 9:
            histogram[n.pitch % 12] += max(1, n.end - n.start)
    if sum(1 for v in histogram if v > 0) < 3:
        return None, 0.0

    best_name, best_score = None, -2.0
    for tonic in range(12):
        rotated = histogram[tonic:] + histogram[:tonic]
        for profile, mode in ((_MAJOR_PROFILE, "major"), (_MINOR_PROFILE, "minor")):
            score = _pearson(rotated, profile)
            if score > best_score:
                best_name, best_score = f"{NOTE_NAMES[tonic]} {mode}", score
    return best_name, best_score


# ---------------------------------------------------------------------- summary


def summarize(mid: mido.MidiFile) -> FileInfo:
    tempos: list[tuple[int, float]] = []
    signatures: list[str] = []
    declared_keys: list[str] = []
    programs: dict[int, tuple[int, int]] = {}  # channel -> (tick, program)
    title: str | None = None

    for ti, track in enumerate(mid.tracks):
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "set_tempo":
                tempos.append((tick, mido.tempo2bpm(msg.tempo)))
            elif msg.type == "time_signature":
                sig = f"{msg.numerator}/{msg.denominator}"
                if sig not in signatures:
                    signatures.append(sig)
            elif msg.type == "key_signature":
                name = _key_name(msg.key)
                if name not in declared_keys:
                    declared_keys.append(name)
            elif msg.type == "program_change":
                earlier = programs.get(msg.channel)
                if earlier is None or tick < earlier[0]:
                    programs[msg.channel] = (tick, msg.program)
            elif msg.type == "track_name" and ti == 0 and title is None:
                cleaned = "".join(c for c in msg.name if c.isprintable()).strip()
                title = cleaned or None

    tempos.sort()
    tempo_declared = bool(tempos)
    if not tempos:
        tempos = [(0, 120.0)]

    notes = extract_notes(mid)
    by_channel: dict[int, list[NoteEvent]] = {}
    for n in notes:
        by_channel.setdefault(n.channel, []).append(n)
    channels = [
        ChannelInfo(
            channel=ch,
            notes=len(chan_notes),
            low=min(n.pitch for n in chan_notes),
            high=max(n.pitch for n in chan_notes),
            program=programs[ch][1] if ch in programs else None,
            single=len(isolated_indices(chan_notes)),
        )
        for ch, chan_notes in sorted(by_channel.items())
    ]

    try:
        length: float | None = mid.length
    except Exception:  # e.g. type 2 files have no single timeline
        length = None

    key, score = estimate_key(notes)
    return FileInfo(
        midi_type=mid.type,
        track_count=len(mid.tracks),
        ticks_per_beat=mid.ticks_per_beat,
        length_seconds=length,
        title=title,
        tempos=tempos,
        tempo_declared=tempo_declared,
        time_signatures=signatures,
        declared_keys=declared_keys,
        estimated_key=key,
        estimated_score=score,
        channels=channels,
    )
