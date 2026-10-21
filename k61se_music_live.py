#!/usr/bin/env python3
"""Live music visualiser for the K61SE, driven from the speaker mix.

The firmware has no music mode and no palette: `op 0x08` is a raw 126-cell RGB
frame (see PROTOCOL.md §5a).  The vendor app does the FFT, picks the bar heights
and colours, and streams the result at ~20 fps.  This tool does the same from
Linux, using PipeWire's monitor of the default sink.  No microphone is used.

    speaker mix -> pw-record -> FFT -> 14 bands -> 14 x 5 bars -> op 0x08

Examples:

    python3 k61se_music_live.py --dry-run            # terminal bars, no device
    python3 k61se_music_live.py --seconds 30         # drive the keyboard
    python3 k61se_music_live.py --seconds 30 --keep-open  # continue live
    python3 k61se_music_live.py --colour FF0000 --seconds 30 --flat
    python3 k61se_music_live.py --palette rainbow --seconds 30
    python3 k61se_music_live.py --design peak --palette fire --seconds 30
    python3 k61se_music_live.py --design psychedelic --palette rainbow
    python3 k61se_music_live.py --design psychedelic --max-brightness
    python3 k61se_music_live.py --design-file designs/music/psychedelic.json

The default palette is the one captured from the vendor app (`music_session_2`):
purple/magenta -> red -> orange -> yellow -> green across the 14 columns,
dimming toward the bottom of each bar.  `--palette rainbow` is the classic
cyan -> green -> yellow -> orange gradient.  `--colour` replaces the palette
with one fixed colour, which is the proof that music frames are not limited to
a rainbow.  Designs: `bars`, `mirror` (bass in the centre), `peak` (falling
peak dots), `pulse` (whole-board bass flash) and `psychedelic` (six morphing
plasma/tunnel/spiral/vortex/ripple/kaleidoscope patterns).

Close the vendor app first: one writer at a time.  Like `k61se_music.py`, this
reads the lighting block before streaming and writes it back at the end.
`--keep-open` continues live rendering after `--seconds` until Ctrl-C; it does
not freeze or re-send the last frame.  `--max-brightness` makes every lit cell
use its full palette colour.
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import time

import numpy as np

import k61se_dev as dev

RATE = 48000
CHANNELS = 1
FFT_SIZE = 2048
HOP = 1024                       # 21.3 ms at 48 kHz
ROWS = 5
COLS = 14
MUSIC_OP = 0x08
FRAME_BYTES = 378

# The app's music palette, copied as wire triples from the brightest cell of
# each column in captures/music_session_2 (evt131).  The wire order is (G,R,B),
# confirmed on the panel 2026-10-19, so these are converted to visible RGB here
# and written back through wire().  The result is the app's own purple -> red ->
# green palette; --palette rainbow is the classic cyan -> orange one.
APP_PALETTE = [
    (0, 195, 255), (0, 199, 255), (0, 208, 255), (0, 221, 255),
    (0, 242, 255), (0, 255, 233), (0, 255, 178), (0, 255, 64),
    (94, 255, 0), (208, 255, 0), (255, 238, 0), (255, 204, 0),
    (255, 187, 0), (255, 149, 0),
]
APP_PALETTE_RGB = [(r, g, b) for (g, r, b) in APP_PALETTE]

PALETTES = {
    "app": (APP_PALETTE_RGB, "column"),
    "rainbow": ([(0, 255, 255), (0, 255, 0), (255, 255, 0), (255, 128, 0)],
                "column"),
    "fire": ([(255, 0, 0), (255, 128, 0), (255, 255, 0)], "column"),
    "ice": ([(0, 0, 255), (0, 255, 255), (255, 255, 255)], "column"),
}
DESIGNS = ("bars", "mirror", "peak", "pulse", "psychedelic")

WINDOW = np.hanning(FFT_SIZE).astype(np.float32)
FREQS = np.fft.rfftfreq(FFT_SIZE, 1.0 / RATE)
EDGES = np.geomspace(50.0, 16000.0, COLS + 1)
BAND_BINS = []
for lo, hi in zip(EDGES[:-1], EDGES[1:]):
    idx = np.where((FREQS >= lo) & (FREQS < hi))[0]
    if not len(idx):
        idx = np.array([int(np.argmin(np.abs(FREQS - lo)))])
    BAND_BINS.append(idx)


# --------------------------------------------------------------------------- #
# audio capture
# --------------------------------------------------------------------------- #
def default_sink_target():
    """Serial of the default sink, or None.

    `pw-record --target` links to a sink's monitor only when given its serial;
    a sink *name* silently falls back to the default source (microphone).
    """
    try:
        out = subprocess.check_output(
            ["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r'object\.serial = "([^"]+)"', out)
    if match:
        return match.group(1)
    match = re.search(r'^id (\d+),', out, re.MULTILINE)
    return match.group(1) if match else None


def describe_target(target):
    """A serial or source name plus its human description, if we can get one."""
    try:
        nodes = json.loads(subprocess.check_output(
            ["pw-dump"], text=True, stderr=subprocess.DEVNULL,
        ))
    except (OSError, subprocess.CalledProcessError, ValueError):
        return str(target)
    for node in nodes:
        if node.get("type") != "PipeWire:Interface:Node":
            continue
        props = node.get("info", {}).get("props", {})
        if str(props.get("object.serial")) == str(target) or \
                props.get("node.name") == str(target):
            desc = props.get("node.description") or props.get("node.name")
            return f"{target} ({desc})" if desc else str(target)
    return str(target)


def open_capture(target):
    cmd = [
        "pw-record",
        "--target", target,
        "--rate", str(RATE),
        "--channels", str(CHANNELS),
        "--format", "s16",
        "--latency", "20ms",
        "-",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, bufsize=0)


def read_exact(fd, size):
    data = bytearray()
    while len(data) < size:
        chunk = os.read(fd, size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


# --------------------------------------------------------------------------- #
# analysis
# --------------------------------------------------------------------------- #
def levels(samples, gain, floor_db, span_db, gamma):
    """14 band levels in 0..1 from the last FFT_SIZE samples."""
    spectrum = np.abs(np.fft.rfft(samples * WINDOW)) / (FFT_SIZE / 2.0)
    db = 20.0 * np.log10(spectrum + 1e-12)
    out = []
    for bins in BAND_BINS:
        value = float(db[bins].max())
        norm = (value - floor_db) / span_db
        norm = min(1.0, max(0.0, norm)) * gain
        out.append(min(1.0, max(0.0, norm)) ** gamma)
    return out


class Smoother:
    """Fast attack, slow release, so bars snap up and fall gracefully."""

    def __init__(self, attack=0.55, release=0.18):
        self.values = [0.0] * COLS
        self.attack = attack
        self.release = release

    def update(self, new):
        for i, value in enumerate(new):
            rate = self.attack if value > self.values[i] else self.release
            self.values[i] += (value - self.values[i]) * rate
        return self.values


# --------------------------------------------------------------------------- #
# frame rendering
# --------------------------------------------------------------------------- #
def put(body, key_index, rgb):
    off = 3 * key_index
    body[off:off + 3] = bytes(rgb)


def parse_colour(text):
    text = text.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError("colour must be RRGGBB")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def wire(rgb, order):
    r, g, b = rgb
    return (g, r, b) if order == "grb" else (r, g, b)


def interpolate(stops, t):
    if len(stops) == 1:
        return stops[0]
    t = min(1.0, max(0.0, t))
    pos = t * (len(stops) - 1)
    index = int(pos)
    if index >= len(stops) - 1:
        return stops[-1]
    frac = pos - index
    a, b = stops[index], stops[index + 1]
    return tuple(int(round(a[i] + (b[i] - a[i]) * frac)) for i in range(3))


class Palette:
    """Colour stops interpolated across columns or across bar height."""

    def __init__(self, stops, mode="column"):
        self.stops = list(stops)
        self.mode = mode

    def at(self, col, row):
        if self.mode == "height":
            t = (ROWS - 1 - row) / (ROWS - 1)
        else:
            t = col / (COLS - 1)
        return interpolate(self.stops, t)


def make_palette(name="app", stops=None, mode=None):
    if stops:
        return Palette(stops, mode or "column")
    base, base_mode = PALETTES.get(name, PALETTES["app"])
    return Palette(base, mode or base_mode)


def parse_stops(values):
    if not isinstance(values, list):
        raise ValueError("palette stops must be a list of hex strings")
    stops = [parse_colour(value) for value in values]
    if not stops:
        raise ValueError("palette needs at least one stop")
    return stops


def load_palette_file(path):
    with open(path) as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return parse_stops(data), "column"
    if isinstance(data, dict):
        mode = data.get("mode", "column")
        if mode not in ("column", "height"):
            raise ValueError("palette mode must be column or height")
        return parse_stops(data.get("stops", [])), mode
    raise ValueError("palette file must be a list or an object")


def load_design_file(path):
    with open(path) as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("design file must be an object")
    design = data.get("design", "bars")
    if design not in DESIGNS:
        raise ValueError(f"design must be one of {', '.join(DESIGNS)}")
    palette = data.get("palette", "app")
    stops = None
    name = "app"
    if isinstance(palette, list):
        stops = parse_stops(palette)
    elif isinstance(palette, str):
        name = palette
        if name not in PALETTES:
            raise ValueError(f"unknown palette {name!r}")
    else:
        raise ValueError("design palette must be a name or a list")
    mode = data.get("mode", "column")
    if mode not in ("column", "height"):
        raise ValueError("design mode must be column or height")
    colour = data.get("colour")
    if colour is not None:
        colour = parse_colour(colour)
    return {
        "name": data.get("name", str(path)),
        "design": design,
        "palette": name,
        "stops": stops,
        "mode": mode,
        "flat": bool(data.get("flat", False)),
        "max_brightness": bool(data.get("max_brightness", False)),
        "colour": colour,
    }


class PeakHold:
    """Peak dots that stay put and then fall at a constant rate."""

    def __init__(self, fall=0.55):
        self.values = [0.0] * COLS
        self.fall = fall

    def update(self, new, dt):
        for i, value in enumerate(new):
            if value >= self.values[i]:
                self.values[i] = value
            else:
                self.values[i] = max(value, self.values[i] - self.fall * dt)
        return self.values


def _psychedelic_field(mode, x, y, radius, angle, p):
    """One continuous scalar field for the psychedelic renderer."""
    if mode == 0:                       # plasma
        return (
            math.sin(x * 3.0 + p * 1.7)
            + math.sin(y * 4.0 - p * 1.3)
            + math.sin((x + y) * 2.0 + p * 0.9)
            + math.sin(radius * 5.0 - p * 2.2)
        )
    if mode == 1:                       # tunnel
        return (
            math.sin(radius * 7.0 - p * 2.8 + angle * 2.0)
            + 0.6 * math.cos(angle * 3.0 + p * 1.1)
            + 0.4 * math.sin((x - y) * 3.0 + p * 1.4)
        )
    if mode == 2:                       # spiral
        return (
            math.sin(angle * 3.0 + radius * 6.0 - p * 2.4)
            + 0.5 * math.sin(radius * 9.0 - p * 3.0)
        )
    if mode == 3:                       # vortex
        swirl = angle + radius * 3.5 - p * 1.6
        return (
            math.sin(swirl * 2.0)
            + 0.6 * math.cos(swirl * 3.0 + p * 0.8)
            + 0.4 * math.sin(radius * 8.0 - p * 2.0)
        )
    if mode == 4:                       # ripple
        return (
            math.sin(radius * 12.0 - p * 4.0)
            + 0.5 * math.sin(radius * 5.0 + p * 2.0)
            + 0.4 * math.cos(angle * 4.0 + p * 1.0)
        )
    # kaleidoscope
    wedge = abs(((angle + p * 0.4) * 3.0 / math.pi) % 2.0 - 1.0)
    return (
        math.sin(wedge * 6.0 + radius * 4.0 - p * 1.5)
        + 0.5 * math.cos(wedge * 10.0 + p * 1.2)
    )


def render_psychedelic(values, palette, colour, order, flat, phase,
                       max_brightness=False):
    """Morphing plasma/tunnel/spiral/vortex/ripple/kaleidoscope field.

    Six patterns cross-fade into each other every seven seconds.  Bass drives
    the rotation speed, overall level drives brightness, and treble shifts the
    palette.  Silence stays black, like the other live designs.
    """
    body = bytearray(FRAME_BYTES)
    if not values or max(values) <= 0.01:
        return bytes(body)

    bass = max(values[:3]) if len(values) >= 3 else max(values)
    mid = sum(values[3:9]) / 6.0 if len(values) >= 9 else 0.0
    treble = sum(values[9:]) / max(1, len(values) - 9)
    level = sum(values) / len(values)
    energy = max(values)
    speed = 0.7 + 1.8 * bass
    p = phase * speed
    period = 7.0
    fade = 1.8
    index = int(phase // period)
    local = phase % period
    blend = 0.0
    if local > period - fade:
        blend = (local - (period - fade)) / fade
        blend = blend * blend * (3.0 - 2.0 * blend)
    mode_a = index % 6
    mode_b = (index + 1) % 6
    centre_x = (COLS - 1) / 2.0
    centre_y = (ROWS - 1) / 2.0

    for col in range(COLS):
        x = (col - centre_x) / 7.0
        for row in range(ROWS):
            y = (row - centre_y) / 2.5
            radius = math.hypot(x, y)
            angle = math.atan2(y, x)
            field_a = _psychedelic_field(
                mode_a, x, y, radius, angle, p)
            field_b = _psychedelic_field(
                mode_b, x, y, radius, angle, p)
            field = field_a * (1.0 - blend) + field_b * blend

            v = 0.5 + 0.5 * math.tanh(field * 0.75)
            shift = (v * 0.7 + phase * 0.06 + bass * 0.18
                     + treble * 0.12) % 1.0
            base = colour if colour is not None else interpolate(
                palette.stops, shift)
            if max_brightness:
                intensity = 1.0
            else:
                intensity = (0.2 + 0.8 * (0.4 * level + 0.35 * energy
                                          + 0.25 * mid))
                intensity *= 0.25 + 0.75 * v
                if not flat:
                    intensity *= 0.6 + 0.4 * (1.0 - row / (ROWS - 1))
            rgb = tuple(max(0, min(255, int(channel * intensity)))
                        for channel in base)
            put(body, 6 * col + 1 + row, wire(rgb, order))

    return bytes(body)


def render(values, design, palette, colour, order, flat, peaks=None, phase=0.0,
           max_brightness=False):
    body = bytearray(FRAME_BYTES)

    if design == "psychedelic":
        return render_psychedelic(values, palette, colour, order, flat, phase,
                                  max_brightness)

    if design == "pulse":
        bass = max(values[:3]) if values else 0.0
        if bass <= 0.0:
            return bytes(body)
        for col in range(COLS):
            for row in range(ROWS):
                base = colour if colour is not None else palette.at(col, row)
                gain = 1.0 if max_brightness else bass
                factor = 1.0 if max_brightness or flat else (
                    1.0 - 0.35 * (row / (ROWS - 1)))
                rgb = tuple(int(c * gain * factor) for c in base)
                put(body, 6 * col + 1 + row, wire(rgb, order))
        return bytes(body)

    if design == "mirror":
        # Fold the 14 bands into seven pairs, then mirror them around the
        # centre: bass in the middle, treble at both edges.  Colour follows the
        # source band, so the palette still runs bass -> treble.
        folded = [max(values[2 * i], values[2 * i + 1])
                  for i in range(COLS // 2)]
        centre = (COLS - 1) / 2.0
        bands = [int(abs(col - centre)) for col in range(COLS)]
        display = [folded[band] for band in bands]
        palette_cols = [int(round(band * (COLS - 1) / (len(folded) - 1)))
                        for band in bands]
    else:
        display = values
        palette_cols = list(range(COLS))

    for col, value in enumerate(display):
        height = int(round(value * ROWS))
        if height <= 0:
            continue
        for row in range(ROWS - height, ROWS):
            base = colour if colour is not None else palette.at(
                palette_cols[col], row)
            rgb = wire(base, order)
            if not max_brightness and not flat:
                pos = (row - (ROWS - height)) / max(height - 1, 1)
                factor = 1.0 - 0.5 * pos
                rgb = tuple(int(c * factor) for c in rgb)
            put(body, 6 * col + 1 + row, rgb)

    if design == "peak" and peaks is not None:
        for col, value in enumerate(peaks):
            peak_height = int(round(value * ROWS))
            if peak_height <= 0:
                continue
            row = max(0, ROWS - peak_height)
            if peak_height > int(round(display[col] * ROWS)):
                put(body, 6 * col + 1 + row, wire((255, 255, 255), order))

    return bytes(body)


BLOCKS = " ▁▂▃▄▅▆▇█"


def show(values):
    line = "".join(BLOCKS[min(8, int(round(v * 8)))] for v in values)
    print(line, flush=True)


# --------------------------------------------------------------------------- #
# device
# --------------------------------------------------------------------------- #
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


def reconnect_device():
    """Try to reopen interface 1 after a transient USB/HID write failure."""
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Live speaker-driven visualiser for the K61SE.",
        epilog="Close the vendor app first; one writer at a time.",
    )
    parser.add_argument("--device", help="source name or sink serial "
                        "(default: the default sink's monitor)")
    parser.add_argument("--seconds", type=float, default=30.0,
                        help="how long to run; 0 = until Ctrl-C (default: 30)")
    parser.add_argument("--fps", type=float, default=25.0,
                        help="frames per second (default: 25)")
    parser.add_argument("--gain", type=float, default=1.0,
                        help="band gain (default: 1.0)")
    parser.add_argument("--floor", type=float, default=-70.0,
                        help="dB floor (default: -70)")
    parser.add_argument("--span", type=float, default=55.0,
                        help="dB range above the floor (default: 55)")
    parser.add_argument("--gamma", type=float, default=0.8,
                        help="level curve; <1 lifts quiet bands (default: 0.8)")
    parser.add_argument("--colour", metavar="RRGGBB",
                        help="fixed colour for every lit cell")
    parser.add_argument("--design", choices=DESIGNS,
                        help="bars, mirror, peak, pulse or psychedelic "
                             "(default: bars)")
    parser.add_argument("--design-file", metavar="PATH",
                        help="JSON design: design, palette, mode, flat, "
                             "max_brightness, colour")
    parser.add_argument("--palette", choices=sorted(PALETTES),
                        help="app, rainbow, fire or ice (default: app)")
    parser.add_argument("--palette-file", metavar="PATH",
                        help="JSON list of #RRGGBB stops, or {stops, mode}")
    parser.add_argument("--mode", choices=("column", "height"),
                        help="colour by column or by bar height")
    parser.add_argument("--order", choices=("grb", "rgb"), default="grb",
                        help="wire order (default: grb, confirmed on panel)")
    parser.add_argument("--flat", action="store_true", default=None,
                        help="no vertical brightness gradient")
    parser.add_argument("--max-brightness", action="store_true",
                        help="lit cells at full palette brightness; no level "
                             "or vertical dimming")
    parser.add_argument("--show", action="store_true",
                        help="print terminal bars while driving the keyboard")
    parser.add_argument("--dry-run", action="store_true",
                        help="terminal bars only; do not open the device")
    parser.add_argument("--no-restore", action="store_true",
                        help="do not rewrite the lighting block; the effect "
                             "returns when the stream stops")
    parser.add_argument("--keep-open", action="store_true",
                        help="after --seconds, keep the live visualizer "
                             "running until Ctrl-C instead of exiting")
    opts = parser.parse_args(argv)

    if opts.seconds < 0:
        parser.error("--seconds must be >= 0")
    if opts.fps <= 0:
        parser.error("--fps must be > 0")

    design_file = {}
    if opts.design_file:
        try:
            design_file = load_design_file(opts.design_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"design file: {exc}")

    design = opts.design or design_file.get("design") or "bars"
    if opts.palette:
        palette_name = opts.palette
        stops = None
    else:
        palette_name = design_file.get("palette") or "app"
        stops = design_file.get("stops")
    mode = opts.mode or design_file.get("mode") or "column"
    if opts.palette_file:
        try:
            stops, file_mode = load_palette_file(opts.palette_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"palette file: {exc}")
        mode = opts.mode or file_mode
    flat = opts.flat if opts.flat is not None else design_file.get("flat", False)
    max_brightness = (opts.max_brightness
                      or design_file.get("max_brightness", False))

    colour = None
    colour_text = opts.colour
    if colour_text:
        try:
            colour = parse_colour(colour_text)
        except ValueError as exc:
            parser.error(str(exc))
    elif design_file.get("colour") is not None:
        colour = design_file["colour"]
        colour_text = "#%02X%02X%02X" % colour

    palette = make_palette(palette_name, stops, mode)
    if colour is not None:
        palette_label = f"single #{colour_text.lstrip('#')}"
    elif opts.palette_file:
        palette_label = f"file {opts.palette_file}"
    elif stops:
        palette_label = f"custom ({len(stops)} stops, {mode})"
    else:
        palette_label = f"{palette_name} ({mode})"

    target = opts.device or default_sink_target()
    if not target:
        print("[!] No default sink found.  Pass --device SERIAL "
              "(see `wpctl status`).", file=sys.stderr)
        return 1
    target_label = describe_target(target)

    if not opts.dry_run:
        check_writers()
        try:
            handle = dev.open_device()
        except RuntimeError as exc:
            print(f"[!] {exc}", file=sys.stderr)
            return 1
        restore_body = None
        if not opts.no_restore:
            try:
                restore_body = dev.read_block("lighting", dev=handle)
            except OSError as exc:
                print(f"[!] Could not read the lighting block: {exc}",
                      file=sys.stderr)
                print("[!] Continuing; the configured effect will return when "
                      "the stream stops.", file=sys.stderr)
    else:
        handle = None
        restore_body = None

    proc = open_capture(target)
    # pw-record takes a moment to link; catch a bad target before streaming.
    time.sleep(0.2)
    if proc.poll() is not None:
        err = proc.stderr.read().decode("utf-8", "replace").strip()
        print(f"[!] pw-record failed: {err or 'no such target'}", file=sys.stderr)
        if handle:
            handle.close()
        return 1

    smoother = Smoother()
    peaks = PeakHold()
    ring = np.zeros(FFT_SIZE, dtype=np.float32)
    interval = 1.0 / opts.fps
    start = time.monotonic()
    next_frame = start
    frames = 0
    failed = False
    shown_at = 0.0
    last_signal = start
    warned_silent = False

    print(f"[+] capturing {target_label}", file=sys.stderr)
    print(f"[+] {opts.fps:g} fps, design {design}, palette "
          f"{palette_label}, gain {opts.gain:g}", file=sys.stderr)

    try:
        while True:
            now = time.monotonic()
            if opts.seconds and now - start >= opts.seconds:
                if opts.keep_open:
                    print("[+] --keep-open: continuing live until Ctrl-C",
                          file=sys.stderr)
                    opts.seconds = 0.0
                else:
                    break
            raw = read_exact(proc.stdout.fileno(), HOP * 2)
            if raw is None:
                err = proc.stderr.read().decode("utf-8", "replace").strip()
                print(f"\n[!] capture ended: {err or 'end of stream'}",
                      file=sys.stderr)
                break
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
            samples /= 32768.0
            ring[:-HOP] = ring[HOP:]
            ring[-HOP:] = samples

            now = time.monotonic()
            if now < next_frame:
                continue
            values = smoother.update(levels(
                ring, opts.gain, opts.floor, opts.span, opts.gamma))
            if max(values) > 0.01:
                last_signal = now
                warned_silent = False
            elif now - last_signal > 3.0 and not warned_silent:
                print(f"[!] no audio on {target_label} for 3 s; "
                      "is anything playing?", file=sys.stderr)
                warned_silent = True
            peak_values = peaks.update(values, interval)
            body = render(values, design, palette, colour, opts.order, flat,
                          peak_values if design == "peak" else None,
                          phase=now - start,
                          max_brightness=max_brightness)
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
                    handle = reconnect_device()
                    if handle is None:
                        print("[!] Could not reopen the lighting interface.",
                              file=sys.stderr)
                        failed = True
                        break
                    continue
                frames += 1
            if opts.show or opts.dry_run:
                if now - shown_at >= 0.08:
                    show(values)
                    shown_at = now
            next_frame += interval
            if next_frame < now:
                next_frame = now + interval
    except KeyboardInterrupt:
        print("\n[+] Interrupted", file=sys.stderr)
    except OSError as exc:
        print(f"\n[!] Device write failed after {frames} frames: {exc}",
              file=sys.stderr)
        failed = True
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()

    elapsed = time.monotonic() - start
    print(f"[+] {frames} frames in {elapsed:.2f} s "
          f"({frames / elapsed if elapsed else 0:.1f} fps)", file=sys.stderr)

    if handle is not None:
        try:
            if restore_body is not None:
                dev.write_block("lighting", restore_body, dev=handle)
                print("[+] Lighting block restored", file=sys.stderr)
            elif not opts.no_restore:
                print("[!] No lighting block captured; the configured effect "
                      "will return.", file=sys.stderr)
        except OSError as exc:
            print(f"[!] Restore failed: {exc}", file=sys.stderr)
            print("[!] Apply a preset to get the effect back: "
                  "python3 k61se_gui.py --apply presets/theme_solid_rainbow.json",
                  file=sys.stderr)
            failed = True
        finally:
            handle.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
