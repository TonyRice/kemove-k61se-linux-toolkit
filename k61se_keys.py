#!/usr/bin/env python3
"""Paint specific keys on the K61SE — static art, no music/audio involved.

`op 0x08` is a raw 126-cell RGB frame (PROTOCOL.md §5a).  A single frame is
transient: the firmware's configured effect takes the LEDs back when the frame
stream stops.  This tool therefore streams the chosen frame continuously by
default (1 fps), exactly like the music overlay.  No microphone, no FFT, and no
dependency on `k61se_music_live.py`.

Examples:

    python3 k61se_keys.py --list-keys
    python3 k61se_keys.py --key W=00FF00 --key A=0000FF --key S=FFFFFF \
        --key D=FF0000                 # stream until Ctrl-C
    python3 k61se_keys.py --file designs/keys/wasd.json
    python3 k61se_keys.py --file designs/keys/lol.json
    python3 k61se_keys.py --text LOL --rainbow
    python3 k61se_keys.py --file designs/keys/wasd.json --hold 30 --restore
    python3 k61se_keys.py --file designs/keys/wasd.json --once
    python3 k61se_keys.py --solid FF0000    # persistent firmware solid, no stream

`--once` sends one frame and exits; the firmware will return to its configured
effect shortly afterwards.  Keep the process running to keep the art.  For a
single uniform colour, `--solid` writes the firmware's own solid effect once,
which persists without any stream.

Key names come from app/K61SE-keyboard.xml.  A name that appears twice
(`Shift`, `Ctrl`, `Alt`) paints both; use `--cell` for one.  `--cell` takes the
frame's `key_index` (1..83 are used; 84..125 are ignored by the firmware).
`--text` draws a short string with a 3x4 block font; `--rainbow` colours
`--fill`, `--all` or `--text` by physical column.

The wire order is `(G,R,B)`, confirmed on the panel 2026-10-19.
"""
import argparse
import colorsys
import json
import signal
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import k61se_dev as dev

MUSIC_OP = 0x08
FRAME_BYTES = 378
ROWS = 5
COLS = 14
KEYMAP_XML = Path(__file__).resolve().parent / "app" / "K61SE-keyboard.xml"

# Variable-width 3x4 block font, drawn into the first four matrix rows.  The
# narrow L lets "LOL" fit as 2 + 1 + 3 + 1 + 2 = 9 columns, centred and clear of
# the bottom-row gaps.
FONT = {
    " ": ("...", "...", "...", "..."),
    "-": ("...", "XXX", "...", "..."),
    ".": ("...", "...", "...", ".X."),
    "!": (".X.", ".X.", "...", ".X."),
    "?": ("XX.", "..X", ".X.", ".X."),
    "0": ("XXX", "X.X", "X.X", "XXX"),
    "1": (".X.", "XX.", ".X.", "XXX"),
    "2": ("XX.", "..X", ".X.", "XXX"),
    "3": ("XX.", "..X", "XX.", "XXX"),
    "4": ("X.X", "X.X", "XXX", "..X"),
    "5": ("XXX", "XX.", "..X", "XXX"),
    "6": ("XXX", "XX.", "X.X", "XXX"),
    "7": ("XXX", "..X", ".X.", ".X."),
    "8": ("XXX", "X.X", "XXX", "XXX"),
    "9": ("XXX", "X.X", "XXX", "..X"),
    "A": ("X.X", "XXX", "X.X", "X.X"),
    "B": ("XX.", "XX.", "X.X", "XXX"),
    "C": ("XXX", "X..", "X..", "XXX"),
    "D": ("XX.", "X.X", "X.X", "XX."),
    "E": ("XXX", "XX.", "X..", "XXX"),
    "F": ("XXX", "XX.", "X..", "X.."),
    "G": ("XXX", "X..", "X.X", "XXX"),
    "H": ("X.X", "XXX", "X.X", "X.X"),
    "I": ("XXX", ".X.", ".X.", "XXX"),
    "J": ("..X", "..X", "X.X", "XXX"),
    "K": ("X.X", "XX.", "X.X", "X.X"),
    "L": ("X.", "X.", "X.", "XX"),
    "M": ("X.X", "XXX", "XXX", "X.X"),
    "N": ("X.X", "XXX", "X.X", "X.X"),
    "O": ("XXX", "X.X", "X.X", "XXX"),
    "P": ("XX.", "X.X", "XX.", "X.."),
    "Q": ("XXX", "X.X", "XXX", "..X"),
    "R": ("XX.", "X.X", "XX.", "X.X"),
    "S": ("XXX", "X..", "..X", "XXX"),
    "T": ("XXX", ".X.", ".X.", ".X."),
    "U": ("X.X", "X.X", "X.X", "XXX"),
    "V": ("X.X", "X.X", "X.X", ".X."),
    "W": ("X.X", "X.X", "XXX", "X.X"),
    "X": ("X.X", ".X.", ".X.", "X.X"),
    "Y": ("X.X", ".X.", ".X.", ".X."),
    "Z": ("XXX", "..X", ".X.", "XXX"),
}


def parse_colour(text):
    text = text.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError("colour must be RRGGBB")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def wire(rgb, order):
    r, g, b = rgb
    return (g, r, b) if order == "grb" else (r, g, b)


def put(body, index, rgb):
    off = 3 * index
    body[off:off + 3] = bytes(rgb)


def load_keys():
    """[(key_index, name, row_col)] sorted by index, from the app's XML."""
    if not KEYMAP_XML.exists():
        raise RuntimeError(f"missing key map: {KEYMAP_XML}")
    root = ET.parse(KEYMAP_XML).getroot()
    keys = []
    for element in root.iter("key"):
        try:
            index = int(element.get("key_index"))
        except (TypeError, ValueError):
            continue
        keys.append((index, element.get("name", "?"),
                     element.get("row_col", "")))
    return sorted(keys)


def name_map(keys):
    mapping = {}
    for index, name, _ in keys:
        mapping.setdefault(name, []).append(index)
    return mapping


def parse_key_arg(text, mapping):
    if "=" not in text:
        raise ValueError("expected NAME=RRGGBB")
    name, colour = text.rsplit("=", 1)
    name = name.strip()
    indices = mapping.get(name)
    if indices is None:
        # tolerate case differences, but not duplicate spellings
        lowered = {key.lower(): key for key in mapping}
        actual = lowered.get(name.lower())
        indices = mapping.get(actual) if actual else None
    if not indices:
        raise ValueError(f"unknown key {name!r}; try --list-keys")
    return indices, parse_colour(colour)


def parse_cell_arg(text):
    if "=" not in text:
        raise ValueError("expected INDEX=RRGGBB")
    index_text, colour = text.split("=", 1)
    index = int(index_text, 0)
    if not 0 <= index < 126:
        raise ValueError("cell index must be 0..125")
    return index, parse_colour(colour)


def rainbow_colour(col):
    """A full-saturation hue for a physical matrix column."""
    hue = col / max(1, COLS - 1)
    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    return (round(r * 255), round(g * 255), round(b * 255))


def apply_all(paint, colour, keys, rainbow=False):
    for index, _, _ in keys:
        col = max(0, (index - 1) // 6)
        paint[index] = rainbow_colour(col) if rainbow else colour


def apply_fill(paint, colour, rainbow=False):
    for col in range(COLS):
        rgb = rainbow_colour(col) if rainbow else colour
        for row in range(ROWS):
            paint[6 * col + 1 + row] = rgb


def text_paint(text, colour, rainbow=False):
    """Render a short string with the variable-width 3x4 font."""
    glyphs = []
    for char in str(text).upper():
        if char not in FONT:
            raise ValueError(f"character {char!r} is not in the 3x4 font")
        glyphs.append(FONT[char])
    if not glyphs:
        raise ValueError("text is empty")
    widths = [len(glyph[0]) for glyph in glyphs]
    width = sum(widths) + len(glyphs) - 1
    if width > COLS:
        raise ValueError(
            f"text is {width} cells wide; maximum is {COLS} (use a shorter "
            f"string or exact --cell art)")
    start = (COLS - width) // 2
    paint = {}
    base = start
    for glyph, glyph_width in zip(glyphs, widths):
        for row, bits in enumerate(glyph):
            for offset, bit in enumerate(bits):
                if bit == "X":
                    col = base + offset
                    index = 6 * col + 1 + row
                    paint[index] = rainbow_colour(col) if rainbow else colour
        base += glyph_width + 1
    return paint


def load_file(path, mapping, keys, rainbow=False):
    with open(path) as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("key design file must be an object")
    rainbow = rainbow or bool(data.get("rainbow", False))
    paint = {}
    if data.get("fill"):
        apply_fill(paint, parse_colour(data["fill"]), rainbow=rainbow)
    if data.get("all"):
        apply_all(paint, parse_colour(data["all"]), keys, rainbow=rainbow)
    if data.get("text"):
        text_colour = parse_colour(data.get("text_colour", "FFFFFF"))
        paint.update(text_paint(data["text"], text_colour, rainbow=rainbow))
    for name, colour in data.get("keys", {}).items():
        indices = mapping.get(name)
        if not indices:
            raise ValueError(f"unknown key {name!r} in {path}")
        for index in indices:
            paint[index] = parse_colour(colour)
    for index, colour in data.get("cells", {}).items():
        index = int(index, 0)
        if not 0 <= index < 126:
            raise ValueError(f"cell index {index} out of range 0..125")
        paint[index] = parse_colour(colour)
    return paint


def build_body(paint, order):
    body = bytearray(FRAME_BYTES)
    for index, rgb in paint.items():
        put(body, index, wire(rgb, order))
    return bytes(body)


def describe(paint, keys):
    by_index = {index: name for index, name, _ in keys}
    parts = []
    for index in sorted(paint):
        name = by_index.get(index, "gap")
        parts.append(f"{name}={index:02d}")
    return ", ".join(parts)


def check_writers():
    others = dev.other_writers()
    if not others:
        return
    print("[!] Another process has the K61SE open:", file=sys.stderr)
    for pid, cmd in others:
        print(f"      pid {pid}: {cmd}", file=sys.stderr)
    print("[!] Close the KEMOVE app (and any Wine device host), then retry.",
          file=sys.stderr)
    raise SystemExit(1)


def _on_sigterm(signum, frame):
    """Turn SIGTERM into Ctrl-C so the restore path still runs."""
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Paint specific K61SE keys (static, no music).",
        epilog="The vendor app must be closed; one writer at a time.",
    )
    parser.add_argument("--key", action="append", default=[], metavar="NAME=HEX",
                        help="key by XML name, e.g. W=00FF00 (repeatable)")
    parser.add_argument("--cell", action="append", default=[], metavar="N=HEX",
                        help="raw key_index 0..125, e.g. 1=FF0000")
    parser.add_argument("--file", metavar="PATH",
                        help="JSON design: keys, cells, all, fill, text, "
                             "text_colour, rainbow")
    parser.add_argument("--all", metavar="HEX",
                        help="paint every key position (61 keys)")
    parser.add_argument("--fill", metavar="HEX",
                        help="paint all 14 x 5 matrix cells (including gaps)")
    parser.add_argument("--text", metavar="TEXT",
                        help="draw a short string as key art (e.g. LOL)")
    parser.add_argument("--text-colour", metavar="RRGGBB", default="FFFFFF",
                        help="text colour (default: FFFFFF)")
    parser.add_argument("--rainbow", action="store_true",
                        help="colour --fill/--all/--text by physical column")
    parser.add_argument("--solid", metavar="RRGGBB",
                        help="write a persistent whole-board firmware solid "
                             "colour and exit (no stream)")
    parser.add_argument("--hold", type=float, default=None, metavar="SECONDS",
                        help="stream for N seconds; 0 = until Ctrl-C "
                             "(default: until Ctrl-C)")
    parser.add_argument("--once", action="store_true",
                        help="send one frame and exit (the effect returns shortly)")
    parser.add_argument("--fps", type=float, default=1.0,
                        help="stream frame rate (default: 1; raise it to 20 if "
                             "the effect shows between frames)")
    parser.add_argument("--restore", action="store_true",
                        help="restore the lighting block after streaming")
    parser.add_argument("--order", choices=("grb", "rgb"), default="grb",
                        help="wire order (default: grb, confirmed)")
    parser.add_argument("--list-keys", action="store_true",
                        help="print key names and key_index values")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the frame without opening the device")
    opts = parser.parse_args(argv)

    if opts.once and opts.hold is not None:
        parser.error("use either --once or --hold, not both")
    if opts.hold is not None and opts.hold < 0:
        parser.error("--hold must be >= 0")
    if opts.fps <= 0:
        parser.error("--fps must be > 0")
    if opts.restore and opts.once:
        parser.error("--restore needs streaming; omit --once")

    paint_args = bool(opts.key or opts.cell or opts.file or opts.all or
                      opts.fill or opts.text)
    if opts.solid:
        if paint_args or opts.rainbow:
            parser.error("--solid is a whole-board firmware colour; do not "
                         "combine it with --key/--cell/--file/--all/--fill/"
                         "--text/--rainbow")
        if opts.hold is not None or opts.once or opts.restore:
            parser.error("--solid is persistent; do not combine it with "
                         "--hold/--once/--restore")
    if opts.rainbow and not (opts.fill or opts.all or opts.text or opts.file):
        parser.error("--rainbow needs --fill, --all, --text or a design file")

    try:
        KEYS = load_keys()
    except (OSError, RuntimeError, ET.ParseError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1
    mapping = name_map(KEYS)

    if opts.list_keys:
        for index, name, row_col in KEYS:
            print(f"{index:3d}  {name:12s} {row_col}")
        return 0

    if opts.solid:
        try:
            solid_colour = parse_colour(opts.solid)
        except ValueError as exc:
            parser.error(str(exc))
        label = "#" + opts.solid.strip().lstrip("#")
        if opts.dry_run:
            print(f"persistent solid {label} -> effect 0x01 (no stream)")
            return 0
        check_writers()
        try:
            handle = dev.open_device()
        except (RuntimeError, OSError) as exc:
            print(f"[!] {exc}", file=sys.stderr)
            return 1
        try:
            report = dev.set_state(
                effect=0x01, rainbow=False, colour=solid_colour, dev=handle)
        except (OSError, ValueError) as exc:
            print(f"[!] Solid write failed: {exc}", file=sys.stderr)
            return 1
        finally:
            handle.close()
        print(f"[+] persistent solid {label} written via effect 0x01")
        for line in report.get("changes", []):
            print(f"    {line}")
        return 0

    paint = {}
    try:
        if opts.fill:
            apply_fill(paint, parse_colour(opts.fill), rainbow=opts.rainbow)
        if opts.all:
            apply_all(paint, parse_colour(opts.all), KEYS,
                      rainbow=opts.rainbow)
        if opts.file:
            paint.update(load_file(opts.file, mapping, KEYS,
                                   rainbow=opts.rainbow))
        if opts.text:
            paint.update(text_paint(opts.text,
                                    parse_colour(opts.text_colour),
                                    rainbow=opts.rainbow))
        for item in opts.key:
            indices, colour = parse_key_arg(item, mapping)
            for index in indices:
                paint[index] = colour
        for item in opts.cell:
            index, colour = parse_cell_arg(item)
            paint[index] = colour
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if not paint:
        parser.error("nothing to paint; use --key, --cell, --file, --all, "
                     "--fill or --text")

    body = build_body(paint, opts.order)
    lit = sum(1 for i in range(126) if any(body[3 * i:3 * i + 3]))

    if opts.dry_run:
        print(f"cells: {lit}  (wire order {opts.order})")
        print(describe(paint, KEYS))
        return 0

    check_writers()
    try:
        handle = dev.open_device()
    except (RuntimeError, OSError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1

    restore_body = None
    streaming = not opts.once
    duration = None if streaming and not opts.hold else (opts.hold if streaming else 0.0)
    try:
        if opts.restore:
            try:
                restore_body = dev.read_block("lighting", dev=handle)
            except OSError as exc:
                print(f"[!] Could not read the lighting block: {exc}",
                      file=sys.stderr)
                print("[!] Not restoring; the configured effect will return.",
                      file=sys.stderr)

        try:
            signal.signal(signal.SIGTERM, _on_sigterm)
        except (ValueError, OSError):
            pass

        if streaming:
            if duration is None:
                print(f"[+] streaming {lit} cells at {opts.fps:g} fps; "
                      "Ctrl-C to stop", file=sys.stderr)
            else:
                print(f"[+] streaming {lit} cells at {opts.fps:g} fps for "
                      f"{duration:g} s", file=sys.stderr)

        start = time.monotonic()
        frames = 0
        try:
            while True:
                dev.write_body(MUSIC_OP, body, dev=handle)
                frames += 1
                if not streaming:
                    break
                if duration is not None and time.monotonic() - start >= duration:
                    break
                time.sleep(1.0 / opts.fps)
        except KeyboardInterrupt:
            print("\n[+] Interrupted", file=sys.stderr)
        except OSError as exc:
            print(f"[!] Send failed after {frames} frame(s): {exc}",
                  file=sys.stderr)
            return 1

        elapsed = time.monotonic() - start
        if streaming:
            print(f"[+] {lit} cells, {frames} frames in {elapsed:.1f}s")
        else:
            print(f"[+] {lit} cells, one frame")

        if restore_body is not None:
            try:
                dev.write_block("lighting", restore_body, dev=handle)
                print("[+] Lighting block restored")
            except OSError as exc:
                print(f"[!] Restore failed: {exc}", file=sys.stderr)
                return 1
        elif opts.restore:
            print("[!] No lighting block captured; the configured effect "
                  "will return.", file=sys.stderr)
        elif streaming:
            print("[!] Stream stopped without --restore; the configured "
                  "effect will return.", file=sys.stderr)
        else:
            print("[!] One frame only; the configured effect will return "
                  "shortly.  Keep the stream running to keep the art.",
                  file=sys.stderr)
    finally:
        handle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
