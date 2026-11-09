"""Turn single notes on one MIDI channel into chords and/or arpeggios."""

from __future__ import annotations

import random
from collections import defaultdict, deque
from dataclasses import dataclass, field

import mido

from .arpeggio import arpeggiate
from .chords import (
    CHORD_TYPES,
    SCALES,
    build_chord,
    build_diatonic_chord,
    chord_keys,
    invert,
)


@dataclass
class Settings:
    """Everything the user can configure. `channel` is 0-based (0-15)."""

    channel: int = 0

    # Where the chord comes from: "fixed", "random" (from random_pool) or "diatonic".
    mode: str = "fixed"
    chord_type: str = "major"
    random_pool: list[str] = field(default_factory=lambda: chord_keys("triad"))
    key_root: int = 0  # 0 = C ... 11 = B (used by diatonic mode and key_lock)
    scale: str = "major"
    diatonic_size: int = 3  # 3 = triads, 4 = seventh chords (diatonic mode)

    # Stay in key: in fixed/random mode, only use chords whose notes all belong to the
    # key. A chord that does not fit is replaced by the diatonic chord on that note.
    key_lock: bool = False

    # Inversions
    inversion: int = 0
    random_inversion: bool = False

    # Arpeggio ("off" gives block chords). See arpeggio.PATTERNS / STEP_SIZES.
    arp_pattern: str = "off"
    arp_step: str = "fit"
    arp_gate: float = 1.0  # fraction of each step that sounds
    arp_probability: float = 1.0  # chance that a chord is arpeggiated

    # Randomness / feel
    apply_probability: float = 1.0  # chance that an eligible note is converted
    only_isolated: bool = True  # skip notes that overlap another note on the channel
    added_velocity_scale: float = 0.85  # velocity of added notes vs. original
    velocity_humanize: int = 0  # +/- random velocity per note
    seed: int | None = None


@dataclass
class Report:
    notes_on_channel: int = 0
    notes_eligible: int = 0
    notes_converted: int = 0
    notes_written: int = 0
    arpeggiated: int = 0
    skipped_not_in_scale: int = 0
    skipped_overlapping: int = 0
    adjusted_to_key: int = 0


@dataclass
class NoteEvent:
    track: int
    on_index: int
    off_index: int | None
    start: int
    end: int
    pitch: int
    velocity: int
    channel: int


# --------------------------------------------------------------------------- reading


def _abs_events(track: mido.MidiTrack) -> list[tuple[int, mido.Message]]:
    t = 0
    events = []
    for msg in track:
        t += msg.time
        events.append((t, msg))
    return events


def _find_notes(
    track_index: int, events: list[tuple[int, mido.Message]], track_end: int
) -> list[NoteEvent]:
    open_notes: dict[tuple[int, int], deque[NoteEvent]] = defaultdict(deque)
    notes: list[NoteEvent] = []
    for idx, (t, msg) in enumerate(events):
        if msg.type == "note_on" and msg.velocity > 0:
            note = NoteEvent(track_index, idx, None, t, track_end, msg.note, msg.velocity, msg.channel)
            open_notes[(msg.channel, msg.note)].append(note)
            notes.append(note)
        elif msg.type in ("note_off", "note_on"):  # note_on with velocity 0 is a note off
            queue = open_notes.get((msg.channel, msg.note))
            if queue:
                note = queue.popleft()
                note.off_index = idx
                note.end = t
    return notes


def extract_notes(mid: mido.MidiFile, channel: int | None = None) -> list[NoteEvent]:
    """All notes in the file (optionally only one channel), with start/end in ticks."""
    notes: list[NoteEvent] = []
    for ti, track in enumerate(mid.tracks):
        events = _abs_events(track)
        track_end = events[-1][0] if events else 0
        for note in _find_notes(ti, events, track_end):
            if channel is None or note.channel == channel:
                notes.append(note)
    return notes


def channel_summary(mid: mido.MidiFile) -> dict[int, int]:
    """Number of notes per channel (0-based channel -> count)."""
    counts: dict[int, int] = defaultdict(int)
    for note in extract_notes(mid):
        counts[note.channel] += 1
    return dict(sorted(counts.items()))


def isolated_indices(notes: list[NoteEvent]) -> set[int]:
    """Indices of notes that do not overlap any other note in the list."""
    order = sorted(range(len(notes)), key=lambda i: (notes[i].start, notes[i].end))
    isolated: set[int] = set()
    max_end = -1
    for pos, i in enumerate(order):
        note = notes[i]
        overlaps_prev = max_end > note.start
        overlaps_next = pos + 1 < len(order) and notes[order[pos + 1]].start < note.end
        if not overlaps_prev and not overlaps_next:
            isolated.add(i)
        max_end = max(max_end, note.end)
    return isolated


# ------------------------------------------------------------------------ chord choice


def _choose_chord(note: NoteEvent, s: Settings, rng: random.Random, report: Report) -> list[int] | None:
    scale = SCALES[s.scale]
    pitch_class = (note.pitch - s.key_root) % 12

    if s.mode == "diatonic":
        pitches = build_diatonic_chord(note.pitch, s.key_root, s.scale, s.diatonic_size)
        if pitches is None:
            report.skipped_not_in_scale += 1
            return None
    else:
        candidates = list(s.random_pool) if s.mode == "random" else [s.chord_type]
        if s.key_lock:
            if pitch_class not in scale:
                report.skipped_not_in_scale += 1
                return None
            fitting = [
                k for k in candidates
                if all((pitch_class + iv) % 12 in scale for iv in CHORD_TYPES[k].intervals)
            ]
            if fitting:
                key = rng.choice(fitting) if s.mode == "random" else fitting[0]
                pitches = build_chord(note.pitch, key)
            else:  # nothing requested fits the key: use the diatonic chord on this note
                size = 4 if any(CHORD_TYPES[k].family == "tetrad" for k in candidates) else 3
                pitches = build_diatonic_chord(note.pitch, s.key_root, s.scale, size)
                report.adjusted_to_key += 1
        else:
            key = rng.choice(candidates) if s.mode == "random" else candidates[0]
            pitches = build_chord(note.pitch, key)

    inversion = rng.randint(0, len(pitches) - 1) if s.random_inversion else s.inversion
    pitches = [p for p in invert(pitches, inversion) if 0 <= p <= 127]
    return pitches if len(pitches) >= 2 else None


def _velocity(note: NoteEvent, pitch: int, s: Settings, rng: random.Random) -> int:
    v = note.velocity if pitch == note.pitch else round(note.velocity * s.added_velocity_scale)
    if s.velocity_humanize:
        v += rng.randint(-s.velocity_humanize, s.velocity_humanize)
    return max(1, min(127, v))


# ------------------------------------------------------------------------- main entry


def _priority(msg: mido.Message) -> int:
    """Ordering for events on the same tick: note-offs, other events, note-ons."""
    if msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
        return 0
    if msg.type == "note_on":
        return 2
    return 1


def transform_midi(
    mid: mido.MidiFile, settings: Settings, rng: random.Random | None = None
) -> tuple[mido.MidiFile, Report]:
    """Return a new MidiFile in which eligible single notes became chords."""
    s = settings
    rng = rng or random.Random(s.seed)
    if s.mode == "random" and not s.random_pool:
        raise ValueError("The random chord pool is empty.")
    if s.mode == "fixed" and s.chord_type not in CHORD_TYPES:
        raise ValueError(f"Unknown chord type: {s.chord_type}")

    tracks_events = [_abs_events(t) for t in mid.tracks]
    track_ends = [ev[-1][0] if ev else 0 for ev in tracks_events]

    notes = []
    for ti, events in enumerate(tracks_events):
        for note in _find_notes(ti, events, track_ends[ti]):
            if note.channel == s.channel:
                notes.append(note)

    report = Report(notes_on_channel=len(notes))
    isolated = isolated_indices(notes)

    removed: set[tuple[int, int]] = set()
    extra: list[list[tuple[int, mido.Message]]] = [[] for _ in tracks_events]

    def emit(track: int, pitch: int, start: int, end: int, velocity: int) -> None:
        extra[track].append((start, mido.Message("note_on", channel=s.channel, note=pitch, velocity=velocity, time=0)))
        extra[track].append((end, mido.Message("note_off", channel=s.channel, note=pitch, velocity=0, time=0)))

    for i, note in enumerate(notes):
        if note.end <= note.start:
            continue
        if s.only_isolated and i not in isolated:
            report.skipped_overlapping += 1
            continue
        report.notes_eligible += 1
        if rng.random() >= s.apply_probability:
            continue
        pitches = _choose_chord(note, s, rng, report)
        if pitches is None:
            continue

        hits: list[tuple[int, int, int]] = []
        if s.arp_pattern != "off" and rng.random() < s.arp_probability:
            hits = arpeggiate(
                pitches, note.start, note.end, mid.ticks_per_beat,
                s.arp_pattern, s.arp_step, s.arp_gate, rng,
            )
        if hits:
            report.arpeggiated += 1
        else:  # block chord (also the fallback when the note is too short to arpeggiate)
            hits = [(p, note.start, note.end) for p in pitches]

        removed.add((note.track, note.on_index))
        if note.off_index is not None:
            removed.add((note.track, note.off_index))
        for pitch, start, end in hits:
            emit(note.track, pitch, start, end, _velocity(note, pitch, s, rng))
        report.notes_converted += 1
        report.notes_written += len(hits)

    out = mido.MidiFile(type=mid.type, ticks_per_beat=mid.ticks_per_beat)
    for ti, events in enumerate(tracks_events):
        items = [
            (t, idx, msg)
            for idx, (t, msg) in enumerate(events)
            if (ti, idx) not in removed and msg.type != "end_of_track"
        ]
        base = len(events)
        items += [(t, base + j, msg) for j, (t, msg) in enumerate(extra[ti])]
        items.sort(key=lambda x: (x[0], _priority(x[2]), x[1]))

        end = max(track_ends[ti], items[-1][0] if items else 0)
        track = mido.MidiTrack()
        prev = 0
        for t, _, msg in items:
            track.append(msg.copy(time=t - prev))
            prev = t
        track.append(mido.MetaMessage("end_of_track", time=end - prev))
        out.tracks.append(track)
    return out, report
