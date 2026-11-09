from midichord.chords import CHORD_TYPES, build_chord, build_diatonic_chord, chord_keys, invert


def test_triads_and_tetrads_present():
    assert len(chord_keys("triad")) >= 6
    assert len(chord_keys("tetrad")) >= 8
    assert all(len(CHORD_TYPES[k].intervals) == 3 for k in chord_keys("triad"))
    assert all(len(CHORD_TYPES[k].intervals) == 4 for k in chord_keys("tetrad"))


def test_major_and_minor():
    assert build_chord(60, "major") == [60, 64, 67]
    assert build_chord(60, "minor") == [60, 63, 67]


def test_inversions():
    assert invert([60, 64, 67], 1) == [64, 67, 72]
    assert invert([60, 64, 67], 2) == [67, 72, 76]
    assert invert([60, 64, 67], 9) == [67, 72, 76]  # clamped


def test_diatonic():
    assert build_diatonic_chord(62, 0, "major") == [62, 65, 69]  # D minor
    assert build_diatonic_chord(71, 0, "major") == [71, 74, 77]  # B diminished
    assert build_diatonic_chord(67, 0, "major", 4) == [67, 71, 74, 77]  # G7
    assert build_diatonic_chord(61, 0, "major") is None  # C# not in C major
