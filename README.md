# MIDI Chord Maker

A full-screen terminal (curses) tool that takes a MIDI file and, on a chosen channel,
turns single notes into chords (optionally arpeggiated, optionally randomised).
A summary of the loaded file (tempo, time signature, key, channels, instruments) stays
on screen next to the settings.

## Getting started (VS Code + Dev Container)

1. Install Docker and the VS Code **Dev Containers** extension.
2. Open this folder in VS Code and choose **Reopen in Container**.
   The container installs the project and its dev tools (`pip install -e '.[dev]'`).
3. In the VS Code terminal run:

   ```bash
   python -m midichord                # press l to load a file
   python -m midichord song.mid       # or pass a file straight away
   ```
   (`midichord` also works as a command.) Press **F5** to debug; the launch
   configurations use the integrated terminal, which curses needs.
4. Run the tests with `pytest` (or the VS Code Testing panel).

Put your `.mid` files inside the project folder so the container can see them.


## Using the interface

```
Up/Down (or k/j)   move between settings        Enter / Space   change the highlighted setting
PgUp / PgDn        jump five rows               p               process and save
l                  load a MIDI file             q               quit
Esc                cancel a pop-up
```

Settings open as pop-ups: pick from a list with Up/Down and Enter, type a number or path,
or tick items with Space in the custom chord pool. The window needs at least 64x18
characters; panels sit side by side from 96 columns, and stack in narrower terminals.

## The file summary

| Item | Where it comes from |
|------|---------------------|
| Tempo (BPM) | `set_tempo` events. Shown as a range if the tempo changes; 120 BPM is the MIDI default when the file has none |
| Time signature | `time_signature` events (optional in the file) |
| Key | A `key_signature` event if the file has one. This is optional and many files omit it, so when it is missing the key is **estimated** from the notes (Krumhansl-Schmuckler algorithm, drums ignored) and labelled as an estimate with its correlation `r` |
| Length | Computed from the tempo map |
| Channels | Note count, pitch range (C4 = middle C) and General MIDI instrument from the program change |

## What it can do

| Menu | Options |
|------|---------|
| Channel | Any of channels 1-16; the file's note counts per channel are shown on load |
| Chord source | **Fixed** type, **Random** from a pool (triads / tetrads / both / all / custom), or **Diatonic** (quality follows a key and scale) |
| Stay in key | On by default. The key is taken from the file (declared or estimated) and can be changed. Fixed/random chords whose notes are all in the key are used as-is; any other chord is replaced by the diatonic chord on that note (e.g. D major in C major becomes D minor, D7 becomes Dm7). Notes outside the key are left alone |
| Chord types | Power; triads: major, minor, dim, aug, sus2, sus4; tetrads: maj7, dom7, min7, minmaj7, dim7, half-dim7, aug7, augmaj7, maj6, min6, add9, 7sus4 |
| Inversion | Root, 1st, 2nd, 3rd or random per chord |
| Arpeggio | up, down, up-down, down-up, shuffled, random pattern per chord; step = *fit* (one pass across the note) or a fixed value 1/4 ... 1/32 incl. triplets; gate; % of chords arpeggiated |
| Randomness | % of notes converted, % of chords arpeggiated, random chord types / inversions / patterns, velocity humanize, seed for repeatable output |
| Other | Only isolated notes (default) vs all notes; velocity of added notes |

## Notes on behaviour

- The original note is the **root** of the chord and keeps its start and length.
- A note is *single* (isolated) if no other note on that channel overlaps it.
  Turn this off in "Other options" to convert every note.
- Diatonic mode leaves notes outside the chosen scale unchanged.
- Notes that would go above MIDI note 127 are dropped.
- Other channels, tempo and controller data are preserved. The input file is never modified;
  output goes to `<name>_chords.mid`.

## Layout

```
midichord/chords.py     chord and scale definitions, inversion
midichord/arpeggio.py   arpeggio patterns and timing
midichord/transform.py  reads the MIDI, applies Settings, writes the result
midichord/midiinfo.py   file summary: tempo, key (declared or estimated), channels
midichord/cli.py        the full-screen curses interface
tests/                  pytest suite
```

On Windows (outside the dev container) curses comes from the `windows-curses` package, which is installed automatically.
