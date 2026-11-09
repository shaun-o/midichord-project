"""Full-screen, curses-based interface.

Layout: a file-summary panel (always visible) next to a settings list. Arrow keys move,
Enter edits the highlighted setting in a pop-up, `p` processes, `l` loads, `q` quits.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    import curses
except ImportError:  # Windows without the 'windows-curses' package
    curses = None  # type: ignore[assignment]

import mido

from . import __version__
from .arpeggio import PATTERNS, STEP_SIZES
from .chords import CHORD_TYPES, NOTE_NAMES, SCALES, chord_keys
from .midiinfo import (
    FileInfo,
    ChannelInfo,
    format_bpm,
    format_duration,
    note_name,
    summarize,
    tempo_text,
)
from .transform import Settings, transform_midi

MIN_H, MIN_W = 18, 64
WIDE_LAYOUT_W = 104  # side-by-side panels from this width upwards
SUMMARY_W = 56
LABEL_W = 21

ATTR: dict[str, int] = {}


def init_attrs() -> None:
    ATTR.update(
        title=curses.A_REVERSE, head=curses.A_BOLD, sel=curses.A_REVERSE, ok=curses.A_BOLD,
        warn=curses.A_BOLD, dim=curses.A_DIM, border=0, button=curses.A_BOLD, field=curses.A_UNDERLINE,
    )
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(2, curses.COLOR_CYAN, bg)
    curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(4, curses.COLOR_GREEN, bg)
    curses.init_pair(5, curses.COLOR_YELLOW, bg)
    ATTR.update(
        title=curses.color_pair(1) | curses.A_BOLD,
        head=curses.color_pair(2) | curses.A_BOLD,
        border=curses.color_pair(2),
        sel=curses.color_pair(3),
        ok=curses.color_pair(4) | curses.A_BOLD,
        button=curses.color_pair(4) | curses.A_BOLD,
        warn=curses.color_pair(5) | curses.A_BOLD,
    )


# --------------------------------------------------------------- drawing helpers


def put(scr, y: int, x: int, text: str, attr: int = 0, width: int | None = None) -> None:
    h, w = scr.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    limit = w - x if width is None else min(width, w - x)
    if limit <= 0:
        return
    try:
        scr.addstr(y, x, text[:limit], attr)
    except curses.error:  # writing the bottom-right cell raises, but still draws
        pass


def fill(scr, y: int, x: int, h: int, w: int) -> None:
    for row in range(h):
        put(scr, y + row, x, " " * w)


def box(scr, y: int, x: int, h: int, w: int, title: str = "") -> None:
    if h < 2 or w < 2:
        return
    attr = ATTR["border"]
    for col in range(x + 1, x + w - 1):
        for row in (y, y + h - 1):
            try:
                scr.addch(row, col, curses.ACS_HLINE, attr)
            except curses.error:
                pass
    for row in range(y + 1, y + h - 1):
        for col in (x, x + w - 1):
            try:
                scr.addch(row, col, curses.ACS_VLINE, attr)
            except curses.error:
                pass
    for (row, col), ch in (
        ((y, x), curses.ACS_ULCORNER), ((y, x + w - 1), curses.ACS_URCORNER),
        ((y + h - 1, x), curses.ACS_LLCORNER), ((y + h - 1, x + w - 1), curses.ACS_LRCORNER),
    ):
        try:
            scr.addch(row, col, ch, attr)
        except curses.error:
            pass
    if title:
        put(scr, y, x + 2, f" {title} ", ATTR["head"], w - 4)


def set_cursor(visible: int) -> None:
    try:
        curses.curs_set(visible)
    except curses.error:
        pass


ENTER_KEYS = (10, 13, curses.KEY_ENTER) if curses else ()
BACKSPACE_KEYS = (curses.KEY_BACKSPACE, 127, 8) if curses else ()


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def chord_label(key: str) -> str:
    c = CHORD_TYPES[key]
    return f"{c.label} ({'-'.join(str(i) for i in c.intervals)})"


@dataclass
class Row:
    kind: str  # "header", "item" or "button"
    label: str
    value: str = ""
    action: Callable[[], None] | None = None
    hint: str = ""

    @property
    def selectable(self) -> bool:
        return self.kind != "header"


# -------------------------------------------------------------------------- app


class App:
    def __init__(self) -> None:
        self.s = Settings(key_lock=True)
        self.mid: mido.MidiFile | None = None
        self.path: Path | None = None
        self.info: FileInfo | None = None
        self.sel = 1
        self.top = 0
        self.status = ""
        self.status_kind = "ok"
        self.running = True
        self.scr = None

    # ---- text for settings values
    def channel_info(self, channel: int) -> ChannelInfo | None:
        if self.info:
            for c in self.info.channels:
                if c.channel == channel:
                    return c
        return None

    def channel_text(self) -> str:
        c = self.channel_info(self.s.channel)
        extra = f"  ({c.instrument})" if c else ("  (no notes)" if self.info else "")
        return f"{self.s.channel + 1}{extra}"

    def pool_text(self) -> str:
        pool = set(self.s.random_pool)
        for name, keys in (
            ("Triads", chord_keys("triad")),
            ("Tetrads", chord_keys("tetrad")),
            ("Triads + tetrads", chord_keys("triad", "tetrad")),
            ("All chord types", chord_keys()),
        ):
            if pool == set(keys):
                return f"{name} ({len(pool)})"
        return f"Custom ({len(pool)} types)"

    def inversion_text(self) -> str:
        if self.s.random_inversion:
            return "Random for each chord"
        return ["Root position", "1st inversion", "2nd inversion", "3rd inversion"][self.s.inversion]

    # ---- rows
    def rows(self) -> list[Row]:
        s = self.s
        R: list[Row] = []

        def head(text: str) -> None:
            R.append(Row("header", text))

        def item(label: str, value: str, action: Callable[[], None], hint: str = "") -> None:
            R.append(Row("item", label, value, action, hint))

        head("Input")
        item("File", self.path.name if self.path else "(none - press Enter)", self.edit_file,
             "Load a MIDI file (shortcut: l)")
        item("Channel", self.channel_text(), self.edit_channel,
             "The MIDI channel (1-16) whose single notes become chords")

        head("Chords")
        item("Chord source", {"fixed": "Fixed type", "random": "Random from a pool",
                              "diatonic": "Diatonic (key + scale)"}[s.mode], self.edit_mode,
             "Fixed: one chord type. Random: pick per note. Diatonic: quality follows the key")
        if s.mode == "fixed":
            item("Chord type", chord_label(s.chord_type), self.edit_chord_type,
                 "Triads, tetrads (7th, 6th, add9...) and power chords")
        elif s.mode == "random":
            item("Random pool", self.pool_text(), self.edit_pool,
                 "The chord types chosen from at random")
        if s.mode != "diatonic":
            item("Stay in key", "Yes" if s.key_lock else "No", self.toggle_key_lock,
                 "Yes: a chord with notes outside the key is swapped for the diatonic chord")
        if s.mode == "diatonic" or s.key_lock:
            suggested = self.info.suggested_key_name if self.info else None
            item("Key", NOTE_NAMES[s.key_root], self.edit_key,
                 f"Tonic of the key. Detected in this file: {suggested}" if suggested else "Tonic of the key")
            item("Scale", s.scale.title(), self.edit_scale,
                 "Notes outside the scale are left as single notes")
        if s.mode == "diatonic":
            item("Chord size", "Triads" if s.diatonic_size == 3 else "Seventh chords",
                 self.edit_size, "Stack 3 or 4 notes in thirds")
        item("Inversion", self.inversion_text(), self.edit_inversion,
             "Which chord note is lowest; random picks one per chord")

        head("Arpeggio")
        item("Pattern", PATTERNS[s.arp_pattern], self.edit_arp_pattern,
             "Off gives block chords; otherwise notes are played one after another")
        if s.arp_pattern != "off":
            item("Step", "Fit to note" if s.arp_step == "fit" else f"{s.arp_step} notes",
                 self.edit_arp_step, "Fit: one pass over the note. Fixed: repeats while it lasts")
            item("Gate", pct(s.arp_gate), self.edit_gate, "How much of each step sounds")
            item("Chords arpeggiated", pct(s.arp_probability), self.edit_arp_prob,
                 "Share of chords arpeggiated; the rest stay block chords")

        head("Randomness")
        item("Notes converted", pct(s.apply_probability), self.edit_apply_prob,
             "Chance that each eligible note becomes a chord")
        item("Velocity humanize", f"+/- {s.velocity_humanize}", self.edit_humanize,
             "Random velocity variation per note")
        item("Random seed", "none (different each run)" if s.seed is None else str(s.seed),
             self.edit_seed, "Set a number to repeat a random result exactly")

        head("Other")
        item("Isolated notes only", "Yes" if s.only_isolated else "No", self.toggle_isolated,
             "Yes: skip notes that overlap another note on the channel")
        item("Added-note velocity", pct(s.added_velocity_scale), self.edit_added_vel,
             "Velocity of the added chord notes relative to the original")

        R.append(Row("button", ">> Process and save", "", self.process,
                     "Apply the settings and write a new MIDI file (shortcut: p)"))
        R.append(Row("button", "Quit", "", self.quit, "Exit (shortcut: q)"))
        return R

    def clamp_selection(self, rows: list[Row]) -> None:
        self.sel = max(0, min(self.sel, len(rows) - 1))
        if not rows[self.sel].selectable:
            for j in range(self.sel, len(rows)):
                if rows[j].selectable:
                    self.sel = j
                    return
            for j in range(self.sel, -1, -1):
                if rows[j].selectable:
                    self.sel = j
                    return

    def move(self, delta: int) -> None:
        rows = self.rows()
        step = 1 if delta > 0 else -1
        for _ in range(abs(delta)):
            j = self.sel + step
            while 0 <= j < len(rows) and not rows[j].selectable:
                j += step
            if 0 <= j < len(rows):
                self.sel = j

    def set_status(self, text: str, kind: str = "ok") -> None:
        self.status, self.status_kind = text, kind

    # ---- summary panel
    def summary_lines(self) -> list[tuple[str, int]]:
        if not self.info or not self.path:
            return [
                ("No MIDI file loaded.", ATTR["warn"]),
                ("", 0),
                ("Press 'l' (or Enter on the File row)", 0),
                ("and type the path to a .mid file.", 0),
            ]
        i = self.info
        lines: list[tuple[str, int]] = []

        def kv(label: str, value: str, attr: int = 0) -> None:
            lines.append((f"{label:<9} {value}", attr))

        kv("File", self.path.name)
        if i.title:
            kv("Title", i.title)
        kv("Tempo", tempo_text(i))
        sig = ", ".join(i.time_signatures) if i.time_signatures else "no time sig"
        length = format_duration(i.length_seconds) if i.length_seconds is not None else "n/a"
        kv("Time", f"{sig}, length {length}")
        if i.declared_keys:
            kv("Key", ", ".join(i.declared_keys) + " (in file)")
            if i.estimated_key and i.estimated_key not in i.declared_keys:
                kv("Key est.", f"{i.estimated_key} (from notes, r={i.estimated_score:.2f})")
        elif i.estimated_key:
            kv("Key", f"{i.estimated_key} (estimated, r={i.estimated_score:.2f})")
        else:
            kv("Key", "not in file; too few notes to estimate")
        kv("Format", f"type {i.midi_type}, {i.track_count} tracks, {i.ticks_per_beat} tpb")
        lines.append((" Ch  Notes Single Range    Instrument", ATTR["head"]))
        for c in i.channels:
            marker = ">" if c.channel == self.s.channel else " "
            rng = f"{note_name(c.low)}-{note_name(c.high)}"
            text = f"{marker}{c.channel + 1:>2} {c.notes:>5} {c.single:>6} {rng:<8} {c.instrument}"
            lines.append((text, ATTR["sel"] if c.channel == self.s.channel else 0))
        return lines

    def draw_summary(self, rect: tuple[int, int, int, int], lines: list[tuple[str, int]]) -> None:
        y, x, h, w = rect
        box(self.scr, y, x, h, w, "MIDI file")
        inner_h, inner_w = h - 2, w - 4
        if len(lines) > inner_h:
            hidden = len(lines) - (inner_h - 1)
            lines = lines[: inner_h - 1] + [(f"... {hidden} more (enlarge terminal)", ATTR["dim"])]
        for n, (text, attr) in enumerate(lines[:inner_h]):
            put(self.scr, y + 1 + n, x + 2, text.ljust(inner_w) if attr == ATTR["sel"] else text,
                attr, inner_w)

    # ---- settings panel
    def draw_settings(self, rect: tuple[int, int, int, int]) -> None:
        y, x, h, w = rect
        rows = self.rows()
        self.clamp_selection(rows)
        box(self.scr, y, x, h, w, "Settings")
        inner_h, inner_w = h - 2, w - 2
        if inner_h < 1:
            return
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + inner_h:
            self.top = self.sel - inner_h + 1
        if self.top > 0 and not rows[self.sel - 1].selectable and self.sel - 1 < self.top:
            self.top = self.sel - 1  # keep the section header above the selection visible
        self.top = max(0, min(self.top, max(0, len(rows) - inner_h)))

        for n in range(inner_h):
            idx = self.top + n
            if idx >= len(rows):
                break
            row = rows[idx]
            yy = y + 1 + n
            if row.kind == "header":
                put(self.scr, yy, x + 1, f"-- {row.label} ".ljust(inner_w, "-"), ATTR["head"], inner_w)
                continue
            text = f" {row.label}" if row.kind == "button" else f" {row.label:<{LABEL_W}}{row.value}"
            text = text[:inner_w].ljust(inner_w)
            if idx == self.sel:
                attr = ATTR["sel"]
            else:
                attr = ATTR["button"] if row.kind == "button" else 0
            put(self.scr, yy, x + 1, text, attr, inner_w)
        if self.top > 0:
            put(self.scr, y, x + w - 11, " ^ more ", ATTR["dim"])
        if self.top + inner_h < len(rows):
            put(self.scr, y + h - 1, x + w - 11, " v more ", ATTR["dim"])

    # ---- whole screen
    def draw(self) -> None:
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        if h < MIN_H or w < MIN_W:
            put(scr, 0, 0, f"Terminal too small: need {MIN_W}x{MIN_H}, have {w}x{h}.", ATTR["warn"])
            return
        put(scr, 0, 0, " " * (w - 1), ATTR["title"])
        put(scr, 0, 1, "MIDI Chord Maker", ATTR["title"])
        version = f"v{__version__}"
        put(scr, 0, w - len(version) - 2, version, ATTR["title"])

        body_h = h - 3
        lines = self.summary_lines()
        if w >= WIDE_LAYOUT_W:
            summary_rect = (1, 0, body_h, SUMMARY_W)
            settings_rect = (1, SUMMARY_W, body_h, w - SUMMARY_W)
        else:
            sh = min(len(lines) + 2, max(6, body_h - 9))
            summary_rect = (1, 0, sh, w)
            settings_rect = (1 + sh, 0, body_h - sh, w)
        self.draw_summary(summary_rect, lines)
        self.draw_settings(settings_rect)

        if self.status:
            put(scr, h - 2, 1, self.status, ATTR[self.status_kind], w - 2)
        else:
            rows = self.rows()
            put(scr, h - 2, 1, rows[self.sel].hint, ATTR["dim"], w - 2)
        put(scr, h - 1, 1, "Up/Down: move   Enter: change   p: process   l: load file   q: quit",
            ATTR["dim"], w - 2)

    # ---- pop-ups
    def _frame(self, title: str, width: int, height: int, footer: str = "") -> tuple[int, int]:
        h, w = self.scr.getmaxyx()
        y, x = max(0, (h - height) // 2), max(0, (w - width) // 2)
        fill(self.scr, y, x, height, width)
        box(self.scr, y, x, height, width, title)
        if footer and len(footer) + 4 < width:
            put(self.scr, y + height - 1, x + 2, f" {footer} ", ATTR["dim"])
        return y, x

    def popup_choose(self, title: str, options: list[tuple[str, str]], current: str | None = None) -> str | None:
        keys = [k for k, _ in options]
        idx = keys.index(current) if current in keys else 0
        top = 0
        while True:
            self.draw()
            h, w = self.scr.getmaxyx()
            width = min(w - 2, max(len(title) + 6, max(len(label) for _, label in options) + 8, 34))
            height = min(len(options) + 2, h - 2)
            inner = height - 2
            if idx < top:
                top = idx
            elif idx >= top + inner:
                top = idx - inner + 1
            y, x = self._frame(title, width, height, "Enter select  Esc cancel")
            for n in range(inner):
                j = top + n
                if j >= len(options):
                    break
                key, label = options[j]
                text = f" {'*' if key == current else ' '} {label}".ljust(width - 2)
                put(self.scr, y + 1 + n, x + 1, text, ATTR["sel"] if j == idx else 0, width - 2)
            self.scr.refresh()
            ch = self.scr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(options)
            elif ch in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(options)
            elif ch == curses.KEY_HOME:
                idx = 0
            elif ch == curses.KEY_END:
                idx = len(options) - 1
            elif ch == curses.KEY_PPAGE:
                idx = max(0, idx - inner)
            elif ch == curses.KEY_NPAGE:
                idx = min(len(options) - 1, idx + inner)
            elif ch in ENTER_KEYS:
                return keys[idx]
            elif ch in (27, ord("q")):
                return None

    def popup_checklist(self, title: str, options: list[tuple[str, str]], selected: set[str]) -> set[str] | None:
        chosen = set(selected)
        idx, top = 0, 0
        while True:
            self.draw()
            h, w = self.scr.getmaxyx()
            width = min(w - 2, max(len(title) + 6, max(len(label) for _, label in options) + 10, 40))
            height = min(len(options) + 2, h - 2)
            inner = height - 2
            if idx < top:
                top = idx
            elif idx >= top + inner:
                top = idx - inner + 1
            y, x = self._frame(title, width, height, "Space toggle  a all  n none  Enter ok  Esc cancel")
            for n in range(inner):
                j = top + n
                if j >= len(options):
                    break
                key, label = options[j]
                text = f" [{'x' if key in chosen else ' '}] {label}".ljust(width - 2)
                put(self.scr, y + 1 + n, x + 1, text, ATTR["sel"] if j == idx else 0, width - 2)
            self.scr.refresh()
            ch = self.scr.getch()
            if ch in (curses.KEY_UP, ord("k")):
                idx = (idx - 1) % len(options)
            elif ch in (curses.KEY_DOWN, ord("j")):
                idx = (idx + 1) % len(options)
            elif ch == ord(" "):
                chosen ^= {options[idx][0]}
            elif ch == ord("a"):
                chosen = {k for k, _ in options}
            elif ch == ord("n"):
                chosen = set()
            elif ch in ENTER_KEYS:
                return chosen
            elif ch in (27, ord("q")):
                return None

    def popup_input(
        self, title: str, label: str, default: str = "",
        validate: Callable[[str], str | None] | None = None,
    ) -> str | None:
        buf = list(default)
        pos = len(buf)
        error = ""
        set_cursor(1)
        try:
            while True:
                self.draw()
                h, w = self.scr.getmaxyx()
                width = min(w - 2, 72)
                y, x = self._frame(title, width, 6, "Enter accept  Esc cancel")
                field_w = width - 4
                offset = max(0, pos - field_w + 1)
                put(self.scr, y + 1, x + 2, label, 0, field_w)
                put(self.scr, y + 2, x + 2, "".join(buf)[offset:offset + field_w].ljust(field_w),
                    ATTR["field"], field_w)
                put(self.scr, y + 3, x + 2, error, ATTR["warn"], field_w)
                self.scr.move(y + 2, x + 2 + pos - offset)
                self.scr.refresh()
                ch = self.scr.getch()
                if ch in ENTER_KEYS:
                    value = "".join(buf).strip()
                    problem = validate(value) if validate else None
                    if problem:
                        error = problem
                        continue
                    return value
                if ch == 27:
                    return None
                error = ""
                if ch in BACKSPACE_KEYS:
                    if pos > 0:
                        del buf[pos - 1]
                        pos -= 1
                elif ch == curses.KEY_DC:
                    if pos < len(buf):
                        del buf[pos]
                elif ch == curses.KEY_LEFT:
                    pos = max(0, pos - 1)
                elif ch == curses.KEY_RIGHT:
                    pos = min(len(buf), pos + 1)
                elif ch == curses.KEY_HOME:
                    pos = 0
                elif ch == curses.KEY_END:
                    pos = len(buf)
                elif ch == 21:  # Ctrl-U
                    buf, pos = [], 0
                elif 32 <= ch < 127:
                    buf.insert(pos, chr(ch))
                    pos += 1
        finally:
            set_cursor(0)

    def popup_int(self, title: str, label: str, lo: int, hi: int, default: int) -> int | None:
        def validate(text: str) -> str | None:
            try:
                value = int(text)
            except ValueError:
                return "Please enter a whole number."
            return None if lo <= value <= hi else f"Enter a number from {lo} to {hi}."

        result = self.popup_input(title, f"{label} ({lo}-{hi})", str(default), validate)
        return None if result is None else int(result)

    def popup_message(self, title: str, lines: list[str], kind: str = "ok") -> None:
        self._message_loop(title, lines, "Press any key", kind, confirm=False)

    def popup_confirm(self, title: str, lines: list[str], default: bool = False) -> bool:
        return self._message_loop(title, lines, "y = yes   n = no", "warn", confirm=True, default=default)

    def _message_loop(self, title, lines, footer, kind, confirm, default=False) -> bool:
        while True:
            self.draw()
            h, w = self.scr.getmaxyx()
            wrap_w = max(20, min(w - 8, 64))
            wrapped = [seg for line in lines for seg in (textwrap.wrap(line, wrap_w) or [""])]
            width = min(w - 2, max(len(title) + 6, max(len(s) for s in wrapped) + 6, len(footer) + 6))
            y, x = self._frame(title, width, min(len(wrapped) + 4, h - 2), footer)
            for n, seg in enumerate(wrapped[: h - 6]):
                put(self.scr, y + 2 + n, x + 3, seg, 0, width - 4)
            self.scr.refresh()
            ch = self.scr.getch()
            if ch == curses.KEY_RESIZE:
                continue
            if not confirm:
                return True
            if ch in (ord("y"), ord("Y")):
                return True
            if ch in (ord("n"), ord("N"), 27):
                return False
            if ch in ENTER_KEYS:
                return default

    # ---- actions: input
    def edit_file(self) -> None:
        default = str(self.path) if self.path else ""
        raw = self.popup_input("Load MIDI file", "Path to a .mid file:", default)
        if raw:
            self.load(raw)

    def load(self, raw_path: str) -> None:
        path = Path(raw_path.strip().strip('"').strip("'")).expanduser()
        try:
            mid = mido.MidiFile(path, clip=True)
            info = summarize(mid)
        except Exception as exc:  # mido raises several different error types
            self.set_status(f"Could not read {path.name}: {exc}", "warn")
            return
        self.mid, self.path, self.info = mid, path, info
        if not info.channels:
            self.set_status(f"Loaded {path.name}, but it contains no notes.", "warn")
            return
        with_notes = [c.channel for c in info.channels]
        if self.s.channel not in with_notes:
            self.s.channel = next((c for c in with_notes if c != 9), with_notes[0])
        suggested = info.suggested_key
        if suggested:
            self.s.key_root, self.s.scale = suggested
            how = "in file" if info.declared_keys else "estimated"
            self.set_status(f"Loaded {path.name}. Key set to {info.suggested_key_name} ({how}); "
                            "change it under Chords.")
        else:
            self.set_status(f"Loaded {path.name}. Could not detect a key - set it under Chords.", "warn")

    def edit_channel(self) -> None:
        options = []
        for ch in range(16):
            c = self.channel_info(ch)
            if c:
                options.append((str(ch), f"Channel {ch + 1:>2}  {c.notes:>4} notes  {c.instrument}"))
            elif self.info:
                options.append((str(ch), f"Channel {ch + 1:>2}  (no notes)"))
            else:
                options.append((str(ch), f"Channel {ch + 1:>2}"))
        choice = self.popup_choose("Channel to process", options, str(self.s.channel))
        if choice is not None:
            self.s.channel = int(choice)

    # ---- actions: chords
    def edit_mode(self) -> None:
        choice = self.popup_choose(
            "Chord source",
            [("fixed", "Fixed type - every note gets the same chord"),
             ("random", "Random - a chord type from a pool, per note"),
             ("diatonic", "Diatonic - quality follows a key and scale")],
            self.s.mode,
        )
        if choice is None:
            return
        self.s.mode = choice
        if choice == "random":
            self.edit_pool()

    def edit_chord_type(self) -> None:
        options = [(k, f"{chord_label(k):<38}{CHORD_TYPES[k].family}") for k in chord_keys()]
        choice = self.popup_choose("Chord type", options, self.s.chord_type)
        if choice:
            self.s.chord_type = choice

    def edit_pool(self) -> None:
        choice = self.popup_choose(
            "Random chord pool",
            [("triads", "Triads"), ("tetrads", "Tetrads (7th, 6th, add9 ...)"),
             ("both", "Triads and tetrads"), ("all", "All, including power chords"),
             ("custom", "Custom selection ...")],
        )
        if choice is None:
            return
        if choice == "custom":
            options = [(k, f"{chord_label(k):<38}{CHORD_TYPES[k].family}") for k in chord_keys()]
            picked = self.popup_checklist("Custom pool", options, set(self.s.random_pool))
            if picked is None:
                return
            if not picked:
                self.popup_message("Empty pool", ["Select at least one chord type."], "warn")
                return
            self.s.random_pool = [k for k in chord_keys() if k in picked]
        else:
            families = {"triads": ("triad",), "tetrads": ("tetrad",),
                        "both": ("triad", "tetrad"), "all": ()}[choice]
            self.s.random_pool = chord_keys(*families)

    def edit_key(self) -> None:
        choice = self.popup_choose("Key", [(str(i), n) for i, n in enumerate(NOTE_NAMES)],
                                   str(self.s.key_root))
        if choice is not None:
            self.s.key_root = int(choice)

    def edit_scale(self) -> None:
        choice = self.popup_choose("Scale", [(k, k.title()) for k in SCALES], self.s.scale)
        if choice:
            self.s.scale = choice

    def edit_size(self) -> None:
        choice = self.popup_choose("Chord size", [("3", "Triads"), ("4", "Seventh chords (tetrads)")],
                                   str(self.s.diatonic_size))
        if choice:
            self.s.diatonic_size = int(choice)

    def edit_inversion(self) -> None:
        choice = self.popup_choose(
            "Inversion",
            [("0", "Root position"), ("1", "1st inversion"),
             ("2", "2nd inversion"), ("3", "3rd inversion (triads use 2nd)"),
             ("random", "Random for each chord")],
            "random" if self.s.random_inversion else str(self.s.inversion),
        )
        if choice is None:
            return
        self.s.random_inversion = choice == "random"
        if not self.s.random_inversion:
            self.s.inversion = int(choice)

    # ---- actions: arpeggio and randomness
    def edit_arp_pattern(self) -> None:
        choice = self.popup_choose("Arpeggio pattern", list(PATTERNS.items()), self.s.arp_pattern)
        if choice:
            self.s.arp_pattern = choice

    def edit_arp_step(self) -> None:
        options = [("fit", "Fit - one pass of the pattern across each note")]
        options += [(k, f"Fixed {k} notes (repeats while the note lasts)") for k in STEP_SIZES]
        choice = self.popup_choose("Arpeggio step", options, self.s.arp_step)
        if choice:
            self.s.arp_step = choice

    def _edit_percent(self, title: str, label: str, attr: str, lo: int = 0, hi: int = 100) -> None:
        value = self.popup_int(title, label, lo, hi, round(getattr(self.s, attr) * 100))
        if value is not None:
            setattr(self.s, attr, value / 100)

    def edit_gate(self) -> None:
        self._edit_percent("Gate", "Percent of each step that sounds", "arp_gate", 10, 100)

    def edit_arp_prob(self) -> None:
        self._edit_percent("Arpeggiated chords", "Percent of chords to arpeggiate", "arp_probability")

    def edit_apply_prob(self) -> None:
        self._edit_percent("Notes converted", "Percent of notes to convert", "apply_probability")

    def edit_added_vel(self) -> None:
        self._edit_percent("Added-note velocity", "Percent of original velocity", "added_velocity_scale", 10, 150)

    def edit_humanize(self) -> None:
        value = self.popup_int("Velocity humanize", "Maximum random +/- velocity", 0, 64,
                               self.s.velocity_humanize)
        if value is not None:
            self.s.velocity_humanize = value

    def edit_seed(self) -> None:
        def validate(text: str) -> str | None:
            if text == "":
                return None
            return None if text.lstrip("-").isdigit() else "Enter a whole number, or leave blank."

        raw = self.popup_input("Random seed", "Seed number (blank = different every run):",
                               "" if self.s.seed is None else str(self.s.seed), validate)
        if raw is not None:
            self.s.seed = int(raw) if raw else None

    def toggle_key_lock(self) -> None:
        self.s.key_lock = not self.s.key_lock

    def toggle_isolated(self) -> None:
        self.s.only_isolated = not self.s.only_isolated

    # ---- process and quit
    def process(self) -> None:
        s = self.s
        if self.mid is None or self.path is None:
            self.popup_message("Nothing to process", ["Load a MIDI file first (press l)."], "warn")
            return
        if self.channel_info(s.channel) is None:
            self.popup_message("No notes", [f"Channel {s.channel + 1} has no notes. Pick another channel."], "warn")
            return
        if s.channel == 9 and not self.popup_confirm(
            "Drum channel", ["Channel 10 is normally drums. Turn drum hits into chords anyway?"]
        ):
            return
        default_out = str(self.path.with_name(f"{self.path.stem}_chords.mid"))
        raw = self.popup_input("Save as", "Output file:", default_out,
                               lambda t: None if t else "Enter a file name.")
        if raw is None:
            return
        out_path = Path(raw).expanduser()
        if out_path.exists() and not self.popup_confirm("File exists", [f"Overwrite {out_path.name}?"]):
            return
        try:
            result, report = transform_midi(self.mid, s)
            result.save(out_path)
        except Exception as exc:
            self.popup_message("Failed", [str(exc)], "warn")
            return
        self.popup_message("Saved", [
            f"{out_path}",
            "",
            f"Notes on channel {s.channel + 1}: {report.notes_on_channel}",
            f"Converted:              {report.notes_converted}",
            f"  of which arpeggiated: {report.arpeggiated}",
            f"Notes written:          {report.notes_written}",
            *([f"Adjusted to fit key:    {report.adjusted_to_key}"] if report.adjusted_to_key else []),
            "",
            *([f"Left alone, overlapping other notes: {report.skipped_overlapping}"]
              if report.skipped_overlapping else []),
            *([f"Left alone, outside the key: {report.skipped_not_in_scale}"]
              if report.skipped_not_in_scale else []),
            *(["", "Overlapping notes are only converted when 'Isolated notes only' is No."]
              if report.skipped_overlapping else []),
        ], "warn" if report.skipped_overlapping or report.skipped_not_in_scale else "ok")
        self.set_status(f"Saved {out_path.name}", "ok")

    def quit(self) -> None:
        self.running = False

    # ---- main loop
    def run(self, stdscr, initial_path: str | None = None) -> None:
        self.scr = stdscr
        set_cursor(0)
        stdscr.keypad(True)
        init_attrs()
        if initial_path:
            self.load(initial_path)
        while self.running:
            self.draw()
            stdscr.refresh()
            ch = stdscr.getch()
            if ch == curses.KEY_RESIZE:
                continue
            self.status = ""
            if ch in (curses.KEY_UP, ord("k")):
                self.move(-1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.move(1)
            elif ch == curses.KEY_PPAGE:
                self.move(-5)
            elif ch == curses.KEY_NPAGE:
                self.move(5)
            elif ch == curses.KEY_HOME:
                self.sel = 0
                self.move(1)
            elif ch == curses.KEY_END:
                self.sel = len(self.rows()) - 1
            elif ch in ENTER_KEYS or ch == ord(" "):
                action = self.rows()[self.sel].action
                if action:
                    action()
            elif ch in (ord("l"), ord("L")):
                self.edit_file()
            elif ch in (ord("p"), ord("P")):
                self.process()
            elif ch in (ord("q"), ord("Q")):
                self.quit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Turn single MIDI notes into chords.")
    parser.add_argument("file", nargs="?", help="MIDI file to load on start-up")
    args = parser.parse_args(argv)
    if curses is None:
        print("The curses module is missing. On Windows run: pip install windows-curses")
        return 1
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("midichord needs an interactive terminal.")
        return 1
    os.environ.setdefault("ESCDELAY", "25")
    try:
        curses.wrapper(App().run, args.file)
    except KeyboardInterrupt:
        pass
    return 0
