#!/usr/bin/env python3
"""Custom host-side typing feedback for the K61SE.

The firmware has its own typing effects (modes 0x04, 0x07 and 0x0C), but this
tool does the feedback on the host:

    standard HID keyboard report -> key_index -> custom 0x08 LED frame

That means the response is ours, not the firmware's: a key can stay lit while
held, leave a fading trail, emit an expanding ripple, or detonate a layered
neon shockwave with sparks and combo waves.  The normal keyboard interface is
only read; typing is not intercepted or blocked.

Examples:

    python3 k61se_typing.py --dry-run --echo
    python3 k61se_typing.py                         # neon storm (default)
    python3 k61se_typing.py --mode storm --rainbow
    python3 k61se_typing.py --mode hold --colour 00FFFF
    python3 k61se_typing.py --mode trail --rainbow
    python3 k61se_typing.py --mode ripple --colour FF00FF --ripple-life 1.2
    python3 k61se_typing.py --seconds 60

The vendor app must be closed: this tool is a lighting writer.  It uses the
transient 0x08 overlay and restores the lighting block on exit.  If the USB
device disappears mid-stream it tries to reopen the interface and carry on.

Key detection is the same as k61se_input.py: interface 0, boot-keyboard report,
six HID usage IDs.  Fn is handled inside the keyboard firmware and may not be
reported.  `--echo` reads the names only; no keystrokes are written to a file.
"""
import argparse
import colorsys
import math
import os
import random
import select
import signal
import sys
import time

import k61se_dev as dev
import k61se_input as inputmod
import k61se_keys as keys

ROWS = 5
COLS = 14
MUSIC_OP = 0x08


def parse_row_col(text):
    """XML row_col is 'row#col' (e.g. A is 2#2)."""
    try:
        row_text, col_text = text.split("#", 1)
        return int(row_text), int(col_text)
    except (AttributeError, ValueError):
        return None


def load_key_cells(keymap):
    """[(key_index, row, col)] for the physical ripple surface."""
    cells = []
    seen = set()
    for index, _name, row_col in keymap.values():
        if index in seen:
            continue
        pos = parse_row_col(row_col)
        if pos is None:
            continue
        seen.add(index)
        cells.append((index, pos[0], pos[1]))
    return cells


def scale(rgb, factor):
    factor = max(0.0, min(1.0, factor))
    return tuple(max(0, min(255, round(channel * factor))) for channel in rgb)


def add_max(paint, index, rgb):
    old = paint.get(index)
    if old is None:
        paint[index] = rgb
    else:
        paint[index] = tuple(max(a, b) for a, b in zip(old, rgb))


def key_colour(index, opts, now=0.0, energy=0.0):
    if opts.rainbow:
        # Golden-ratio hue spacing means adjacent keys are visibly different.
        hue = (index * 0.618033988749895) % 1.0
    elif opts.random:
        return (random.randrange(32, 256), random.randrange(32, 256),
                random.randrange(32, 256))
    elif opts.mode == "storm" and not getattr(opts, "colour_given", False):
        # A slow global hue drift plus a per-key offset; fast typing pushes
        # the palette toward hotter colours.
        hue = (now * 0.12 + index * 0.045 + energy * 0.35) % 1.0
    else:
        return opts.colour

    red, green, blue = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    return (round(red * 255), round(green * 255), round(blue * 255))


def storm_global_colour(opts, now, energy):
    if getattr(opts, "colour_given", False):
        return opts.colour
    hue = (now * 0.11 + energy * 0.30) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    return (round(red * 255), round(green * 255), round(blue * 255))


class TypingFeedback:
    def __init__(self, keymap, opts):
        self.keymap = keymap
        self.opts = opts
        self.cells = load_key_cells(keymap)
        self.positions = {index: (row, col)
                          for index, row, col in self.cells}
        self.cell_lookup = {(row, col): index
                            for index, row, col in self.cells}
        self.held = {}          # key_index -> (name, rgb)
        self.fades = []         # (key_index, rgb, expires_at)
        self.ripples = []       # (origin_row, origin_col, rgb, born_at)
        self.blooms = []        # (key_index, rgb, born_at, life)
        self.shockwaves = []    # (row, col, rgb, born, life, speed, width)
        self.sparks = []        # (row, col, vrow, vcol, rgb, born, life)
        self.press_times = []
        self.energy = 0.0
        self.last_render = None
        self.last_combo_wave = 0.0
        self.previous_codes = set()

    def echo(self, verb, name, index, code):
        if self.opts.echo:
            print(f"{verb:<7} {name:<9} key_index={index:<3} "
                  f"code=0x{code:02X}", flush=True)

    def _storm_press(self, index, row_col, colour, now):
        """Layer a core bloom, a shockwave, sparks, and combo waves."""
        self.blooms.append((index, colour, now, 0.42))
        self.press_times = [when for when in self.press_times
                            if now - when <= 1.5]
        self.press_times.append(now)

        pos = parse_row_col(row_col)
        if pos is not None:
            origin_row, origin_col = pos
            self.shockwaves.append(
                (origin_row, origin_col, colour, now, 0.65, 7.0, 0.9))
            for _ in range(3):
                angle = random.random() * math.tau
                speed = random.uniform(3.0, 7.0)
                self.sparks.append((
                    origin_row, origin_col,
                    math.sin(angle) * speed, math.cos(angle) * speed,
                    colour, now, random.uniform(0.35, 0.65),
                ))

        # Five fast presses release a white wave across the whole board.
        combo = len(self.press_times)
        if combo >= 5 and combo % 5 == 0 and now - self.last_combo_wave > 0.35:
            self.last_combo_wave = now
            self.energy = min(1.0, self.energy + 0.35)
            self.shockwaves.append((2, 7, (255, 255, 255),
                                    now, 1.15, 11.0, 1.25))
            self.blooms.append((35, (255, 255, 255), now, 0.30))

        self.blooms = self.blooms[-24:]
        self.shockwaves = self.shockwaves[-16:]
        self.sparks = self.sparks[-48:]

    def handle_report(self, codes, now):
        """Apply one HID report and spawn feedback for new/released keys."""
        pressed = codes - self.previous_codes
        released = self.previous_codes - codes
        self.previous_codes = codes

        for code in sorted(pressed):
            item = self.keymap.get(code)
            if item is None:
                if self.opts.echo:
                    print(f"press   ?         code=0x{code:02X}", flush=True)
                continue
            index, name, row_col = item

            if self.opts.mode == "storm":
                self.energy = min(1.0, self.energy + 0.22)
                colour = key_colour(index, self.opts, now, self.energy)
                self.held[index] = (name, colour)
                self._storm_press(index, row_col, colour, now)
            else:
                colour = key_colour(index, self.opts, now, self.energy)
                self.held[index] = (name, colour)
            self.echo("press", name, index, code)

            # Pressing a key again cancels its old fade immediately.
            self.fades = [fade for fade in self.fades if fade[0] != index]

            if self.opts.mode == "ripple":
                pos = parse_row_col(row_col)
                if pos is not None:
                    self.ripples.append((pos[0], pos[1], colour, now))

        for code in sorted(released):
            item = self.keymap.get(code)
            if item is None:
                continue
            index, name, _row_col = item
            colour = self.held.pop(index, (name, None))[1]
            self.echo("release", name, index, code)
            if self.opts.mode == "trail" and colour is not None:
                self.fades.append((index, colour, now + self.opts.fade))

    def _render_storm(self, paint, now):
        global_colour = storm_global_colour(self.opts, now, self.energy)

        # A very dim moving nebula, so the board stays alive while typing.
        if self.energy > 0.02:
            for index, row, col in self.cells:
                wave = 0.5 + 0.5 * math.sin(
                    now * 2.6 + col * 0.72 - row * 0.51)
                factor = (0.03 + 0.10 * self.energy) * wave
                add_max(paint, index, scale(global_colour, factor))

        live = []
        for index, colour, born, life in self.blooms:
            age = now - born
            if age >= life:
                continue
            live.append((index, colour, born, life))
            fade = max(0.0, 1.0 - age / life) ** 1.6
            pos = self.positions.get(index)
            if pos is None:
                add_max(paint, index, scale(colour, fade))
                continue
            origin_row, origin_col = pos
            for index2, row, col in self.cells:
                distance = abs(col - origin_col) + abs(row - origin_row)
                if distance <= 2:
                    add_max(paint, index2,
                            scale(colour, fade * (1.0 - distance / 3.0)))
        self.blooms = live

        live = []
        for (origin_row, origin_col, colour, born,
             life, speed, width) in self.shockwaves:
            age = now - born
            if age >= life:
                continue
            live.append((origin_row, origin_col, colour, born,
                         life, speed, width))
            radius = age * speed
            fade = max(0.0, 1.0 - age / life) ** 1.2
            for index, row, col in self.cells:
                distance = abs(col - origin_col) + abs(row - origin_row)
                delta = abs(distance - radius)
                if delta <= width:
                    add_max(paint, index,
                            scale(colour, fade * (1.0 - delta / width)))
        self.shockwaves = live

        live = []
        for row, col, vrow, vcol, colour, born, life in self.sparks:
            age = now - born
            if age >= life:
                continue
            live.append((row, col, vrow, vcol, colour, born, life))
            key = self.cell_lookup.get(
                (round(row + vrow * age), round(col + vcol * age)))
            if key is not None:
                add_max(paint, key,
                        scale(colour, max(0.0, 1.0 - age / life) ** 2))
        self.sparks = live

    def render(self, now):
        paint = {}

        if self.opts.mode == "storm":
            if self.last_render is not None:
                dt = max(0.0, now - self.last_render)
                self.energy = max(0.0, self.energy - 0.55 * dt)
            self.last_render = now
            self.press_times = [when for when in self.press_times
                                if now - when <= 1.5]

        for index, (_name, colour) in self.held.items():
            if self.opts.mode == "storm":
                pulse = 0.78 + 0.22 * math.sin(now * 9.0 + index * 1.7)
                add_max(paint, index, scale(colour, pulse))
            else:
                add_max(paint, index, colour)

        if self.opts.mode == "trail":
            live = []
            for index, colour, expires in self.fades:
                if expires <= now:
                    continue
                live.append((index, colour, expires))
                add_max(paint, index,
                        scale(colour, (expires - now) / self.opts.fade))
            self.fades = live

        if self.opts.mode == "ripple":
            live = []
            for origin_row, origin_col, colour, born in self.ripples:
                age = now - born
                if age >= self.opts.ripple_life:
                    continue
                live.append((origin_row, origin_col, colour, born))
                radius = age * self.opts.ripple_speed
                fade = 1.0 - age / self.opts.ripple_life
                width = 0.9
                for index, row, col in self.cells:
                    distance = abs(col - origin_col) + abs(row - origin_row)
                    delta = abs(distance - radius)
                    if delta > width:
                        continue
                    add_max(paint, index,
                            scale(colour, fade * (1.0 - delta / width)))
            self.ripples = live

        if self.opts.mode == "storm":
            self._render_storm(paint, now)

        return paint


def _on_sigterm(signum, frame):
    raise KeyboardInterrupt


def open_input(device):
    node = device or inputmod.find_keyboard_node()
    return node, os.open(node, os.O_RDONLY)


def reconnect_vendor():
    """Try a few times to reopen the vendor interface after a USB hiccup."""
    for attempt in range(5):
        time.sleep(0.4)
        try:
            handle = dev.open_device()
            print("[+] Reconnected to the K61SE lighting interface",
                  file=sys.stderr)
            return handle
        except (RuntimeError, OSError):
            if attempt == 4:
                break
    return None


def restore_lighting(handle, restore_body, no_restore):
    """Restore the captured lighting block; return the handle still usable.

    The original handle may be dead after the same USB hiccup that stopped the
    stream, so try a fresh handle once.  Returning None means restore failed.
    """
    if no_restore or restore_body is None:
        print("[!] No lighting restore; the configured effect will return.",
              file=sys.stderr)
        return handle

    try:
        dev.write_block("lighting", restore_body, dev=handle)
        print("[+] Lighting block restored", file=sys.stderr)
        return handle
    except OSError:
        pass

    try:
        handle.close()
    except Exception:
        pass
    handle = reconnect_vendor()
    if handle is None:
        print("[!] Restore failed. The transient overlay will end when this "
              "process exits.", file=sys.stderr)
        return None
    try:
        dev.write_block("lighting", restore_body, dev=handle)
        print("[+] Lighting block restored", file=sys.stderr)
        return handle
    except OSError:
        print("[!] Restore failed. The transient overlay will end when this "
              "process exits.", file=sys.stderr)
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Custom host-side typing feedback for the K61SE.",
        epilog="The vendor app must be closed; one lighting writer at a time.")
    parser.add_argument("--mode", choices=("storm", "hold", "trail", "ripple"),
                        default="storm",
                        help="storm: neon shockwaves + combo waves (default); "
                             "hold: lit while held; trail: fade after release; "
                             "ripple: expanding rings")
    parser.add_argument("--colour", metavar="RRGGBB", default=None,
                        help="fixed key colour; storm is dynamic by default")
    parser.add_argument("--rainbow", action="store_true",
                        help="colour each key by its key_index")
    parser.add_argument("--random", action="store_true",
                        help="new random colour for each press")
    parser.add_argument("--fade", type=float, default=0.35,
                        help="trail fade time in seconds (default: 0.35)")
    parser.add_argument("--ripple-life", type=float, default=0.9,
                        help="ripple lifetime in seconds (default: 0.9)")
    parser.add_argument("--ripple-speed", type=float, default=8.0,
                        help="ripple radius in cells per second (default: 8)")
    parser.add_argument("--fps", type=float, default=30.0,
                        help="LED frame rate (default: 30)")
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="stop after N seconds; 0 = until Ctrl-C")
    parser.add_argument("--order", choices=("grb", "rgb"), default="grb",
                        help="wire channel order (default: grb)")
    parser.add_argument("--echo", action="store_true",
                        help="print press/release events (names only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="read keys, but do not open the lighting interface")
    parser.add_argument("--no-restore", action="store_true",
                        help="do not rewrite the lighting block on exit")
    parser.add_argument("--input-device", metavar="PATH",
                        help="/dev/hidrawN keyboard override")
    opts = parser.parse_args(argv)

    if opts.rainbow and opts.random:
        parser.error("--rainbow and --random are mutually exclusive")
    if opts.fade <= 0:
        parser.error("--fade must be > 0")
    if opts.ripple_life <= 0:
        parser.error("--ripple-life must be > 0")
    if opts.ripple_speed <= 0:
        parser.error("--ripple-speed must be > 0")
    if opts.fps <= 0:
        parser.error("--fps must be > 0")
    if opts.seconds < 0:
        parser.error("--seconds must be >= 0")

    try:
        opts.colour_given = opts.colour is not None
        opts.colour = keys.parse_colour(opts.colour or "FFFFFF")
    except ValueError as exc:
        parser.error(str(exc))

    try:
        keymap = inputmod.load_keymap()
    except (OSError, RuntimeError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1
    feedback = TypingFeedback(keymap, opts)

    handle = None
    restore_body = None
    if not opts.dry_run:
        try:
            keys.check_writers()
        except SystemExit:
            return 1
        try:
            handle = dev.open_device()
        except (RuntimeError, OSError) as exc:
            print(f"[!] Could not open the K61SE lighting interface: {exc}",
                  file=sys.stderr)
            return 1
        if not opts.no_restore:
            try:
                restore_body = dev.read_block("lighting", dev=handle)
            except OSError as exc:
                print(f"[!] Could not capture the lighting block: {exc}",
                      file=sys.stderr)
                print("[!] Continuing without restore.", file=sys.stderr)

    try:
        node, kbd_fd = open_input(opts.input_device)
    except (RuntimeError, OSError) as exc:
        print(f"[!] Could not open the keyboard interface: {exc}",
              file=sys.stderr)
        if handle is not None:
            handle.close()
        return 1

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        pass

    print(f"[+] reading {node}; mode {opts.mode}, {opts.fps:g} fps"
          + (" (dry-run, no LED writes)" if opts.dry_run else ""),
          file=sys.stderr)
    print("[+] press keys; Ctrl-C to stop", file=sys.stderr)

    interval = 1.0 / opts.fps
    start = time.monotonic()
    next_frame = start
    frames = 0
    failed = False
    kbd_node_label = node

    try:
        while True:
            now = time.monotonic()
            if opts.seconds and now - start >= opts.seconds:
                break

            timeout = max(0.0, next_frame - now)
            ready, _, _ = select.select([kbd_fd], [], [], timeout)

            if ready:
                try:
                    data = os.read(kbd_fd, 64)
                    if not data:
                        raise OSError("keyboard interface closed")
                except OSError as exc:
                    print(f"[!] Keyboard read failed: {exc}; reconnecting",
                          file=sys.stderr)
                    try:
                        os.close(kbd_fd)
                    except OSError:
                        pass
                    try:
                        kbd_node_label, kbd_fd = open_input(opts.input_device)
                        print(f"[+] Reopened {kbd_node_label}", file=sys.stderr)
                    except (RuntimeError, OSError) as reopen_exc:
                        print(f"[!] Keyboard reconnect failed: {reopen_exc}",
                              file=sys.stderr)
                        failed = True
                        break
                else:
                    codes = inputmod.decode_report(data)
                    if codes is not None:
                        feedback.handle_report(codes, time.monotonic())

            now = time.monotonic()
            if now < next_frame:
                continue

            paint = feedback.render(now)
            body = keys.build_body(paint, opts.order)
            if handle is not None:
                try:
                    dev.write_body(MUSIC_OP, body, dev=handle)
                except OSError as exc:
                    print(f"\n[!] LED write failed after {frames} frames: {exc}",
                          file=sys.stderr)
                    try:
                        handle.close()
                    except Exception:
                        pass
                    handle = reconnect_vendor()
                    if handle is None:
                        print("[!] Could not reopen the lighting interface.",
                              file=sys.stderr)
                        failed = True
                        break
            frames += 1
            next_frame += interval
            if next_frame < now:
                next_frame = now + interval
    except KeyboardInterrupt:
        print("\n[+] Interrupted", file=sys.stderr)
    finally:
        try:
            os.close(kbd_fd)
        except OSError:
            pass

    elapsed = time.monotonic() - start
    print(f"[+] {frames} frames in {elapsed:.2f} s "
          f"({frames / elapsed if elapsed else 0:.1f} fps)", file=sys.stderr)

    if handle is not None:
        if not opts.dry_run:
            handle = restore_lighting(handle, restore_body, opts.no_restore)
            if handle is None:
                failed = True
        if handle is not None:
            handle.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
