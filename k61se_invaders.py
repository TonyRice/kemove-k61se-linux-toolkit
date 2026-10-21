#!/usr/bin/env python3
"""A tiny Space Invaders animation for the K61SE's 14 x 5 LED matrix.

This is deliberately host-side pixel art: three invaders, a moving ship, a
shot, and a short explosion.  It uses the same transient `0x08` frame as
`k61se_keys.py`, but changes the frame every tick instead of repeating one
static design.

    python3 k61se_invaders.py --dry-run          # ASCII preview, no device
    python3 k61se_invaders.py --seconds 30       # play for 30 seconds
    python3 k61se_invaders.py --layout tall      # bigger, ship on row 2
    python3 k61se_invaders.py                    # play until Ctrl-C
    python3 k61se_invaders.py --no-restore       # leave the effect alone

The vendor app must be closed; one writer at a time.  The default `top` layout
stays on the two full-width rows, runs at 5 fps, and restores the lighting block
when it stops.  `--layout tall` uses rows 0-2, with the ship on row 2.
"""
import argparse
import signal
import sys
import time

import k61se_dev as dev
import k61se_keys as keys

ROWS = 5
COLS = 14
FPS = 5.0
CYCLE = 15

ALIEN_BASES = (1, 5, 9)
OFFSETS = (0, 1, 2, 1)
ALIEN_TOP_A = ("XXX",)
ALIEN_TOP_B = ("X.X",)
ALIEN_TALL_A = ("XXX", ".X.")
ALIEN_TALL_B = (".X.", "XXX")
SHIP_ROWS = {"top": 1, "tall": 2}
BULLET_ROWS = {"top": (1, 0, 0), "tall": (2, 1, 0)}

EXPLOSION_START = 3
EXPLOSION_FRAMES = 6

ALIEN = (0, 255, 0)
SHIP = (0, 255, 255)
SHIP_CORE = (255, 255, 255)
BULLET = (255, 255, 0)
EXPLOSION_RED = (255, 0, 0)
EXPLOSION_ORANGE = (255, 128, 0)
EXPLOSION_YELLOW = (255, 255, 128)

SYMBOLS = {
    ALIEN: "A",
    SHIP: "S",
    SHIP_CORE: "S",
    BULLET: "*",
    EXPLOSION_RED: "X",
    EXPLOSION_ORANGE: "X",
    EXPLOSION_YELLOW: "o",
}


def cell_index(col, row):
    """Matrix cell -> key_index, or None for an out-of-range cell."""
    if 0 <= col < COLS and 0 <= row < ROWS:
        return 6 * col + 1 + row
    return None


def set_pixel(paint, col, row, colour):
    index = cell_index(col, row)
    if index is not None:
        paint[index] = colour


def draw_sprite(paint, left, top, sprite, colour):
    for row, bits in enumerate(sprite):
        for offset, bit in enumerate(bits):
            if bit == "X":
                set_pixel(paint, left + offset, top + row, colour)


def draw_explosion(paint, centre, age):
    bursts = (
        (
            (centre, 0, EXPLOSION_RED),
            (centre - 1, 0, EXPLOSION_ORANGE),
            (centre + 1, 0, EXPLOSION_ORANGE),
            (centre, 1, EXPLOSION_YELLOW),
        ),
        (
            (centre - 1, 0, EXPLOSION_RED),
            (centre + 1, 0, EXPLOSION_RED),
            (centre, 1, EXPLOSION_ORANGE),
            (centre - 1, 1, EXPLOSION_YELLOW),
            (centre + 1, 1, EXPLOSION_YELLOW),
        ),
        (
            (centre, 0, EXPLOSION_YELLOW),
            (centre - 2, 0, EXPLOSION_RED),
            (centre + 2, 0, EXPLOSION_RED),
            (centre, 1, EXPLOSION_ORANGE),
            (centre - 1, 1, EXPLOSION_RED),
            (centre + 1, 1, EXPLOSION_RED),
        ),
        (
            (centre - 1, 0, EXPLOSION_ORANGE),
            (centre + 1, 0, EXPLOSION_ORANGE),
            (centre, 1, EXPLOSION_RED),
        ),
        (
            (centre - 1, 1, EXPLOSION_RED),
            (centre + 1, 1, EXPLOSION_RED),
            (centre, 0, EXPLOSION_ORANGE),
        ),
        (
            (centre - 2, 0, EXPLOSION_ORANGE),
            (centre + 2, 0, EXPLOSION_ORANGE),
            (centre, 1, EXPLOSION_RED),
        ),
    )
    for col, row, colour in bursts[min(age, len(bursts) - 1)]:
        set_pixel(paint, col, row, colour)


def frame(step, layout="top"):
    """Return {key_index: (r, g, b)} for one animation frame.

    `top` keeps the whole scene in the two full-width rows.  `tall` gives the
    invaders a second row and puts the ship on row 2, so it reads larger but
    uses one middle row.
    """
    if layout not in SHIP_ROWS:
        raise ValueError(f"unknown layout {layout!r}")

    phase = step % CYCLE
    if phase < EXPLOSION_START + EXPLOSION_FRAMES:
        offset = 0
    else:
        offset = OFFSETS[((phase - 9) // 2 + 1) % len(OFFSETS)]
    centre = 6 + offset

    if layout == "tall":
        sprite = ALIEN_TALL_A if (step // 2) % 2 == 0 else ALIEN_TALL_B
        ship_row = SHIP_ROWS[layout]
    else:
        sprite = ALIEN_TOP_A if (step // 2) % 2 == 0 else ALIEN_TOP_B
        ship_row = SHIP_ROWS[layout]

    exploding = EXPLOSION_START <= phase < EXPLOSION_START + EXPLOSION_FRAMES
    paint = {}

    # The middle invader vanishes for the blast; the two outer ones remain.
    for number, base in enumerate(ALIEN_BASES):
        if number == 1 and exploding:
            continue
        draw_sprite(paint, base + offset, 0, sprite, ALIEN)

    # Ship follows the middle column; the layout decides which row it uses.
    for col in range(centre - 1, centre + 2):
        set_pixel(paint, col, ship_row, SHIP)
    set_pixel(paint, centre, ship_row, SHIP_CORE)

    if phase < EXPLOSION_START:
        set_pixel(paint, centre, BULLET_ROWS[layout][phase], BULLET)
    elif exploding:
        draw_explosion(paint, centre, phase - EXPLOSION_START)

    return paint


def preview(paint):
    lines = []
    for row in range(ROWS):
        line = ""
        for col in range(COLS):
            index = cell_index(col, row)
            colour = paint.get(index) if index is not None else None
            line += "." if colour is None else SYMBOLS.get(colour, "#")
        lines.append(line)
    return "\n".join(lines)


def _on_sigterm(signum, frame_number):
    raise KeyboardInterrupt


def dry_run(layout="top"):
    print(f"layout: {layout}\nA=alien  S=ship  *=shot  X/o=explosion\n")
    for step in range(CYCLE):
        print(f"frame {step:02d}  phase {step % CYCLE:02d}")
        print(preview(frame(step, layout)))
        print()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Animated Space Invaders demo for the K61SE LED matrix.",
        epilog="The vendor app must be closed; one writer at a time.",
    )
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="play for N seconds; 0 = until Ctrl-C (default: 0)")
    parser.add_argument("--fps", type=float, default=FPS,
                        help=f"animation frame rate (default: {FPS:g})")
    parser.add_argument("--layout", choices=("top", "tall"), default="top",
                        help="top = two full-width rows; tall = invaders on "
                             "rows 0-1 and ship on row 2 (default: top)")
    parser.add_argument("--order", choices=("grb", "rgb"), default="grb",
                        help="wire order (default: grb, confirmed)")
    parser.add_argument("--no-restore", action="store_true",
                        help="do not restore the lighting block on exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="print ASCII frames and do not open the device")
    opts = parser.parse_args(argv)

    if opts.seconds < 0:
        parser.error("--seconds must be >= 0")
    if opts.fps <= 0:
        parser.error("--fps must be > 0")

    if opts.dry_run:
        dry_run(opts.layout)
        return 0

    keys.check_writers()
    try:
        handle = dev.open_device()
    except (OSError, RuntimeError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1

    restore_body = None
    error = None
    try:
        if not opts.no_restore:
            try:
                restore_body = dev.read_block("lighting", dev=handle)
            except OSError as exc:
                print(f"[!] Could not read the lighting block: {exc}",
                      file=sys.stderr)
                print("[!] Continuing; use --no-restore if you do not want a "
                      "restore attempt at the end.", file=sys.stderr)

        try:
            signal.signal(signal.SIGTERM, _on_sigterm)
        except (OSError, ValueError):
            pass

        interval = 1.0 / opts.fps
        start = time.monotonic()
        next_at = start
        frames = 0
        print(f"[+] invaders ({opts.layout}) at {opts.fps:g} fps"
              + (f" for {opts.seconds:g} s" if opts.seconds else " until Ctrl-C"))
        try:
            while True:
                if opts.seconds and time.monotonic() - start >= opts.seconds:
                    break
                body = keys.build_body(frame(frames, opts.layout), opts.order)
                dev.write_body(keys.MUSIC_OP, body, dev=handle)
                frames += 1
                next_at += interval
                delay = next_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
        except KeyboardInterrupt:
            print("\n[+] Interrupted", file=sys.stderr)
        except OSError as exc:
            error = exc

        elapsed = time.monotonic() - start
        print(f"[+] {frames} frames in {elapsed:.1f}s "
              f"({frames / elapsed if elapsed else 0:.1f} fps)")
        if error is not None:
            print(f"[!] Send failed: {error}", file=sys.stderr)

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

    return 1 if error is not None else 0


if __name__ == "__main__":
    sys.exit(main())
