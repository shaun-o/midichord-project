import random

import mido

from midichord.transform import Settings, channel_summary, extract_notes, transform_midi

TPB = 480


def make_midi(notes):
    """notes: list of (channel, pitch, start, end)."""
    mid = mido.MidiFile(ticks_per_beat=TPB)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    events = []
    for ch, p, s, e in notes:
        events.append((s, mido.Message("note_on", channel=ch, note=p, velocity=100)))
        events.append((e, mido.Message("note_off", channel=ch, note=p, velocity=0)))
    events.sort(key=lambda x: x[0])
    prev = 0
    for t, m in events:
        track.append(m.copy(time=t - prev))
        prev = t
    track.append(mido.MetaMessage("end_of_track", time=0))
    return mid


def pitches_at(mid, ch, start):
    return sorted(n.pitch for n in extract_notes(mid, ch) if n.start == start)


def test_fixed_major_chord_and_other_channels_untouched():
    mid = make_midi([(0, 60, 0, 480), (0, 62, 480, 960), (1, 40, 0, 960)])
    out, report = transform_midi(mid, Settings(channel=0, chord_type="major"))
    assert pitches_at(out, 0, 0) == [60, 64, 67]
    assert pitches_at(out, 0, 480) == [62, 66, 69]
    assert [n.pitch for n in extract_notes(out, 1)] == [40]
    assert report.notes_converted == 2 and report.notes_written == 6
    for n in extract_notes(out, 0):
        assert n.end in (480, 960)


def test_only_isolated_skips_overlapping():
    mid = make_midi([(0, 60, 0, 480), (0, 64, 240, 720), (0, 72, 1000, 1480)])
    out, report = transform_midi(mid, Settings(only_isolated=True, chord_type="minor"))
    assert report.notes_converted == 1
    assert pitches_at(out, 0, 1000) == [72, 75, 79]
    out2, report2 = transform_midi(mid, Settings(only_isolated=False))
    assert report2.notes_converted == 3


def test_arpeggio_up_fit_is_sequential():
    mid = make_midi([(0, 60, 0, 480)])
    out, _ = transform_midi(mid, Settings(arp_pattern="up", arp_step="fit"))
    notes = sorted(extract_notes(out, 0), key=lambda n: n.start)
    assert [n.pitch for n in notes] == [60, 64, 67]
    assert notes[0].start == 0 and notes[-1].end == 480
    for a, b in zip(notes, notes[1:]):
        assert a.end <= b.start


def test_arpeggio_fixed_step_repeats():
    mid = make_midi([(0, 60, 0, 1920)])  # 4 beats
    out, _ = transform_midi(mid, Settings(arp_pattern="up", arp_step="1/8"))
    assert len(extract_notes(out, 0)) == 8


def test_tetrad_and_inversion():
    mid = make_midi([(0, 60, 0, 480)])
    out, _ = transform_midi(mid, Settings(chord_type="dom7", inversion=1))
    assert pitches_at(out, 0, 0) == [64, 67, 70, 72]


def test_probability_zero_changes_nothing():
    mid = make_midi([(0, 60, 0, 480), (0, 62, 480, 960)])
    out, report = transform_midi(mid, Settings(apply_probability=0.0))
    assert report.notes_converted == 0
    assert [n.pitch for n in extract_notes(out, 0)] == [60, 62]


def test_random_is_repeatable_with_seed():
    mid = make_midi([(0, 48 + i, i * 480, i * 480 + 480) for i in range(20)])
    s = Settings(mode="random", random_inversion=True, arp_pattern="random",
                 arp_probability=0.5, apply_probability=0.7, velocity_humanize=10, seed=42)
    a, _ = transform_midi(mid, s)
    b, _ = transform_midi(mid, s)
    assert [m for t in a.tracks for m in t] == [m for t in b.tracks for m in t]
    s.seed = 43
    c, _ = transform_midi(mid, s)
    assert [m for t in a.tracks for m in t] != [m for t in c.tracks for m in t]


def test_diatonic_skips_out_of_scale():
    mid = make_midi([(0, 62, 0, 480), (0, 61, 480, 960)])
    out, report = transform_midi(mid, Settings(mode="diatonic"))
    assert pitches_at(out, 0, 0) == [62, 65, 69]
    assert report.skipped_not_in_scale == 1
    assert pitches_at(out, 0, 480) == [61]


def test_output_is_valid_midi_file(tmp_path):
    mid = make_midi([(0, 60, 0, 480), (3, 50, 0, 480)])
    out, _ = transform_midi(mid, Settings(mode="random"))
    path = tmp_path / "x.mid"
    out.save(path)
    assert channel_summary(mido.MidiFile(path)).keys() == {0, 3}


# ------------------------------------------------------------- stay in key / skipping

from midichord.chords import SCALES, parse_key  # noqa: E402


def in_key(pitches, root=0, scale="major"):
    return all((p - root) % 12 in SCALES[scale] for p in pitches)


def test_key_lock_swaps_clashing_chord_for_diatonic_one():
    mid = make_midi([(0, 62, 0, 480), (0, 60, 480, 960)])   # D and C in C major
    out, report = transform_midi(mid, Settings(chord_type="major", key_lock=True))
    assert pitches_at(out, 0, 0) == [62, 65, 69]      # D major (has F#) -> D minor
    assert pitches_at(out, 0, 480) == [60, 64, 67]    # C major fits, kept
    assert report.adjusted_to_key == 1


def test_key_lock_tetrads_use_seventh_chords():
    mid = make_midi([(0, 62, 0, 480), (0, 67, 480, 960)])
    out, _ = transform_midi(mid, Settings(chord_type="dom7", key_lock=True))
    assert pitches_at(out, 0, 0) == [62, 65, 69, 72]    # Dm7 instead of D7 (F#)
    assert pitches_at(out, 0, 480) == [67, 71, 74, 77]  # G7 is diatonic, kept


def test_key_lock_skips_notes_outside_key():
    mid = make_midi([(0, 61, 0, 480)])
    out, report = transform_midi(mid, Settings(key_lock=True))
    assert pitches_at(out, 0, 0) == [61]
    assert report.skipped_not_in_scale == 1


def test_random_with_key_lock_never_leaves_the_key():
    notes = [(0, p, i * 480, i * 480 + 480) for i, p in enumerate([60, 62, 64, 65, 67, 69, 71] * 6)]
    mid = make_midi(notes)
    for scale_root, scale in ((0, "major"), (9, "natural minor")):
        s = Settings(mode="random", key_lock=True, key_root=scale_root, scale=scale, seed=1,
                     random_pool=["major", "minor", "dim", "aug", "sus2", "sus4", "dom7", "maj7", "dim7"],
                     random_inversion=True, arp_pattern="random")
        out, _ = transform_midi(mid, s)
        assert in_key([n.pitch for n in extract_notes(out, 0)], scale_root, scale)


def test_key_lock_off_allows_chromatic_notes():
    mid = make_midi([(0, 62, 0, 480)])
    out, _ = transform_midi(mid, Settings(chord_type="major", key_lock=False))
    assert pitches_at(out, 0, 0) == [62, 66, 69]


def test_overlapping_notes_are_counted_not_silently_dropped():
    # Reproduces "stops after bar 4": the last two notes overlap each other.
    mid = make_midi([(0, 60, 0, 480), (0, 72, 1920, 2880), (0, 76, 2400, 3360)])
    out, report = transform_midi(mid, Settings())
    assert report.notes_converted == 1 and report.skipped_overlapping == 2
    assert pitches_at(out, 0, 1920) == [72]
    out, report = transform_midi(mid, Settings(only_isolated=False))
    assert report.notes_converted == 3 and report.skipped_overlapping == 0


def test_parse_key():
    assert parse_key("C major") == (0, "major")
    assert parse_key("A minor") == (9, "natural minor")
    assert parse_key("Bb major") == (10, "major")
    assert parse_key("F# minor") == (6, "natural minor")
    assert parse_key("nonsense") is None
