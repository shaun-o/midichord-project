import mido

from midichord.midiinfo import estimate_key, format_bpm, format_duration, note_name, summarize
from midichord.transform import extract_notes

TPB = 480


def build(track_msgs):
    mid = mido.MidiFile(ticks_per_beat=TPB)
    track = mido.MidiTrack(track_msgs)
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(track)
    return mid


def note(ch, pitch, dur=480, vel=90):
    return [mido.Message("note_on", channel=ch, note=pitch, velocity=vel, time=0),
            mido.Message("note_off", channel=ch, note=pitch, velocity=0, time=dur)]


def test_tempo_time_signature_declared_key_and_title():
    msgs = [
        mido.MetaMessage("track_name", name="My Song"),
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(90)),
        mido.MetaMessage("time_signature", numerator=3, denominator=4),
        mido.MetaMessage("key_signature", key="Am"),
        mido.Message("program_change", channel=0, program=40),
        *note(0, 60),
    ]
    info = summarize(build(msgs))
    assert info.title == "My Song"
    assert info.tempo_declared and round(info.tempos[0][1]) == 90
    assert info.time_signatures == ["3/4"]
    assert info.declared_keys == ["A minor"]
    assert info.channels[0].instrument == "Violin"
    assert info.length_seconds is not None and abs(info.length_seconds - 480 / 480 * 60 / 90) < 0.01


def test_defaults_when_nothing_declared():
    info = summarize(build(note(0, 60)))
    assert not info.tempo_declared and info.tempos[0][1] == 120.0
    assert info.time_signatures == [] and info.declared_keys == []


def test_tempo_changes_are_collected():
    msgs = [mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100)),
            *note(0, 60, 960),
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(140)),
            *note(0, 62)]
    info = summarize(build(msgs))
    assert [round(b) for _, b in info.tempos] == [100, 140]


def test_channel_ranges_and_drums():
    msgs = [*note(0, 48), *note(0, 72), *note(9, 36)]
    info = summarize(build(msgs))
    ch0 = next(c for c in info.channels if c.channel == 0)
    assert (ch0.notes, ch0.low, ch0.high) == (2, 48, 72)
    assert next(c for c in info.channels if c.channel == 9).instrument.startswith("Drums")


def test_key_estimation():
    # C major material: tonic/third/fifth held longest, plus the other scale tones.
    msgs = []
    for pitch, dur in [(60, 1920), (64, 960), (67, 1440), (62, 480), (65, 480), (69, 480), (71, 480)]:
        msgs += note(0, pitch, dur)
    key, score = estimate_key(extract_notes(build(msgs)))
    assert key == "C major" and score > 0.7

    msgs = []
    for pitch, dur in [(57, 1920), (60, 960), (64, 1440), (59, 480), (62, 480), (65, 480), (68, 480)]:
        msgs += note(0, pitch, dur)
    assert estimate_key(extract_notes(build(msgs)))[0] == "A minor"


def test_too_few_notes_gives_no_estimate():
    assert estimate_key(extract_notes(build([*note(0, 60), *note(0, 64)])))[0] is None


def test_formatting():
    assert note_name(60) == "C4" and note_name(21) == "A0"
    assert format_bpm(100.0) == "100" and format_bpm(97.5) == "97.5"
    assert format_duration(75.4) == "1:15"


def test_tempo_text():
    from midichord.midiinfo import tempo_text

    assert tempo_text(summarize(build(note(0, 60)))).startswith("120 BPM (default")
    one = build([mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100)), *note(0, 60)])
    assert tempo_text(summarize(one)) == "100 BPM"
    two = build([mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100)), *note(0, 60, 960),
                 mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120)), *note(0, 62)])
    assert tempo_text(summarize(two)) == "100-120 BPM (varies, starts at 100)"


def test_single_note_count_and_suggested_key():
    msgs = [mido.MetaMessage("key_signature", key="Bb"), *note(0, 60)]
    # two overlapping notes
    msgs += [mido.Message("note_on", channel=0, note=64, velocity=90, time=0),
             mido.Message("note_on", channel=0, note=67, velocity=90, time=100),
             mido.Message("note_off", channel=0, note=64, velocity=0, time=100),
             mido.Message("note_off", channel=0, note=67, velocity=0, time=100)]
    info = summarize(build(msgs))
    assert info.channels[0].notes == 3 and info.channels[0].single == 1
    assert info.suggested_key == (10, "major")
