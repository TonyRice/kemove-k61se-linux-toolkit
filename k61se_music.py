#!/usr/bin/env python3
"""Stream live LED frames (op 0x08) to the K61SE, music-style.

The vendor app's music mode is not a firmware mode: it simply starts sending
378-byte `0x08` feature-report bodies and stops when it wants the effect back
(see PROTOCOL.md §5a).  This tool does the same thing from Python:

    frame header : 06 08 00 00 01 00 7A 01      (report 6, op 0x08, 378 bytes)
    body         : 126 cells x (G, R, B), column-major
                   byte = 3 * key_index from app/K61SE-keyboard.xml
                   key_index = 6*col + 1 + row, cols 0..13, rows 0..4

Only the app may write while it is open, so this tool refuses to run if another
process holds a K61SE node (the vendor app under Wine counts).  It reads the
lighting block before streaming and writes it back afterwards, which is exactly
what the app does on exit (`music_exit` capture: 0x84 read, then a byte-
identical 0x04 write).

Patterns:

    test   Four keys that reveal the channel order.  With the frame order the
           captures imply, (G, R, B):
               Esc   -> green
               Tab   -> red
               Caps  -> blue
               Space -> white
           If Esc comes out red and Tab green, the order is (R, G, B).

    bars   Fourteen full-height rainbow bars, one per keyboard column.  Useful
           for checking the matrix mapping without relying on colour order.

    off    All-zero frame (blank panel), useful for checking takeover.

    solid  All 14 x 5 cells in one colour.  `--colour RRGGBB` picks it and
           `--order grb|rgb` chooses the wire order; a whole-board red is the
           quickest way to see whether the channels match.

Examples:

    python3 k61se_music.py --dry-run
    python3 k61se_music.py --pattern test --seconds 5
    python3 k61se_music.py --pattern solid --colour FF0000 --seconds 5
    python3 k61se_music.py --pattern bars --seconds 10 --no-restore

Only run this with the vendor app closed.  Frames are a transient overlay: when
the stream stops, the firmware resumes the configured effect, so restoring the
lighting block is on by default.
"""
import argparse
import colorsys
import sys
import time

import k61se_dev as dev

MUSIC_OP = 0x08
ROWS = 5
COLS = 14
FPS = 20.0


def _put(body, key_index, g, r, b):
    """Write one cell.  The wire order is (G, R, B), matching the colour block."""
    off = 3 * key_index
    body[off:off + 3] = bytes((g & 0xFF, r & 0xFF, b & 0xFF))


def _parse_colour(text):
    text = text.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError("colour must be RRGGBB")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def _wire(rgb, order):
    r, g, b = rgb
    return (g, r, b) if order == "grb" else (r, g, b)


def solid_pattern(rgb, order):
    """All 14 x 5 cells in one colour; a whole-board colour-order check."""
    g, r, b = _wire(rgb, order)
    body = bytearray(378)
    for col in range(14):
        for row in range(5):
            _put(body, 6 * col + 1 + row, g, r, b)
    return bytes(body)


def test_pattern():
    body = bytearray(378)
    _put(body, 1, 255, 0, 0)      # Esc  -> frame channel 0
    _put(body, 2, 0, 255, 0)      # Tab  -> frame channel 1
    _put(body, 3, 0, 0, 255)      # Caps -> frame channel 2
    _put(body, 35, 255, 255, 255)  # Space -> white sanity check
    return bytes(body)


def bars_pattern():
    body = bytearray(378)
    for col in range(COLS):
        r, g, b = colorsys.hsv_to_rgb(col / COLS, 1.0, 1.0)
        r, g, b = int(r * 255), int(g * 255), int(b * 255)
        for row in range(ROWS):
            _put(body, 6 * col + 1 + row, g, r, b)
    return bytes(body)


PATTERNS = {
    "test": test_pattern,
    "bars": bars_pattern,
    "off": lambda: bytes(378),
}

LEGEND = """\
  Esc   (cell  1)  frame (G=255,R=0,  B=0  )  green if (G,R,B), red if (R,G,B)
  Tab   (cell  2)  frame (G=0,  R=255,B=0  )  red   if (G,R,B), green if (R,G,B)
  Caps  (cell  3)  frame (G=0,  R=0,  B=255)  blue either way
  Space (cell 35)  frame (G=255,R=255,B=255)  white either way
"""


def show_dry_run(pattern, body):
    print(f"pattern: {pattern}  body: {len(body)} bytes")
    lit = [(i, body[3 * i:3 * i + 3]) for i in range(126)
           if any(body[3 * i:3 * i + 3])]
    print(f"non-zero cells: {len(lit)}")
    if pattern == "test":
        print(LEGEND)
    if pattern == "solid":
        print("solid: every 14 x 5 cell carries the same triple")
    limit = 4 if pattern == "solid" else 20
    for cell, rgb in lit[:limit]:
        print(f"  cell {cell:3d}: G={rgb[0]:3d} R={rgb[1]:3d} B={rgb[2]:3d}")
    if len(lit) > limit:
        print(f"  ... {len(lit) - limit} more")
    print("dry run: no device opened, nothing sent")


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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Stream op 0x08 live LED frames to the K61SE (music-style).",
        epilog="The vendor app must be closed; one writer at a time.",
    )
    parser.add_argument("--pattern", choices=sorted(PATTERNS) + ["solid"],
                        default="test",
                        help="frame content (default: test)")
    parser.add_argument("--colour", metavar="RRGGBB", default="FFFFFF",
                        help="colour for --pattern solid (default: FFFFFF)")
    parser.add_argument("--order", choices=("grb", "rgb"), default="grb",
                        help="wire order for --pattern solid (default: grb)")
    parser.add_argument("--seconds", type=float, default=5.0,
                        help="how long to stream; 0 = until Ctrl-C (default: 5)")
    parser.add_argument("--fps", type=float, default=FPS,
                        help=f"frames per second (default: {FPS:g})")
    parser.add_argument("--no-restore", action="store_true",
                        help="do not rewrite the lighting block; the configured "
                             "effect returns when the stream stops")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and print the frame, do not touch the device")
    opts = parser.parse_args(argv)

    if opts.seconds < 0:
        parser.error("--seconds must be >= 0")
    if opts.fps <= 0:
        parser.error("--fps must be > 0")

    pattern = opts.pattern
    if pattern == "solid":
        try:
            colour = _parse_colour(opts.colour)
        except ValueError as exc:
            parser.error(str(exc))
        body = solid_pattern(colour, opts.order)
    else:
        body = PATTERNS[pattern]()

    if opts.dry_run:
        show_dry_run(pattern, body)
        return 0

    check_writers()

    try:
        handle = dev.open_device()
    except RuntimeError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1

    restore_body = None
    try:
        if not opts.no_restore:
            try:
                restore_body = dev.read_block("lighting", dev=handle)
            except OSError as exc:
                print(f"[!] Could not read the lighting block: {exc}",
                      file=sys.stderr)
                print("[!] Continuing; use --no-restore if you do not want a "
                      "restore attempt at the end.", file=sys.stderr)

        frames = 0
        start = time.monotonic()
        next_at = start
        interval = 1.0 / opts.fps
        print(f"[+] Streaming '{pattern}' at {opts.fps:g} fps"
              + (f" for {opts.seconds:g} s" if opts.seconds else " until Ctrl-C"))
        try:
            while True:
                now = time.monotonic()
                if opts.seconds and now - start >= opts.seconds:
                    break
                dev.write_body(MUSIC_OP, body, dev=handle)
                frames += 1
                next_at += interval
                delay = next_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
        except KeyboardInterrupt:
            print("\n[+] Interrupted")
        except OSError as exc:
            print(f"\n[!] Send failed after {frames} frames: {exc}",
                  file=sys.stderr)
            return 1
        elapsed = time.monotonic() - start
        print(f"[+] Sent {frames} frames in {elapsed:.2f} s "
              f"({frames / elapsed if elapsed else 0:.1f} fps)")

        if restore_body is not None:
            try:
                dev.write_block("lighting", restore_body, dev=handle)
                print("[+] Lighting block restored")
            except OSError as exc:
                print(f"[!] Restore failed: {exc}", file=sys.stderr)
                print("[!] Apply a preset to get the effect back: "
                      "python3 k61se_gui.py --apply presets/theme_solid_rainbow.json",
                      file=sys.stderr)
                return 1
        elif not opts.no_restore:
            print("[!] No lighting block captured; the configured effect "
                  "will return.", file=sys.stderr)
    finally:
        handle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
