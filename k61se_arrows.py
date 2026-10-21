#!/usr/bin/env python3
"""Sticky arrow layer for the K61SE, using evdev + uinput.

The keyboard's Fn layer turns these four keys into arrows while Fn is held:

    / ?        Up
    Right Alt  Left
    Menu       Down  (Linux reports this key as Compose, code 127)
    Right Ctrl Right

This daemon grabs the K61SE's normal evdev keyboard node, watches for a
double-tap of CapsLock, and toggles a virtual layer:

    normal  -> / ?  Right Alt  Menu/Compose  Right Ctrl
    arrows  -> Up   Left       Down          Right

It creates a uinput virtual keyboard and forwards every other key unchanged.
A single CapsLock tap still sends CapsLock (after the double-tap window), and
a double-tap toggles arrow mode without changing CapsLock.

The daemon must be able to read the keyboard event node and write
``/dev/uinput``.  Run it with sudo for the first test, or add a udev/uaccess
rule.  It does not write to the keyboard firmware and does not require the
vendor app.

Examples:

    python3 k61se_arrows.py --print-mapping
    sudo python3 k61se_arrows.py
    sudo python3 k61se_arrows.py --window-ms 350
    sudo python3 k61se_arrows.py --single-tap none
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import glob
import os
import select
import signal
import struct
import sys
import time

# Linux input event constants.
EV_SYN = 0x00
EV_KEY = 0x01
EV_REP = 0x14
SYN_REPORT = 0x00

KEY_CAPSLOCK = 58
KEY_SLASH = 53
KEY_RIGHTCTRL = 97
KEY_RIGHTALT = 100
KEY_UP = 103
KEY_LEFT = 105
KEY_RIGHT = 106
KEY_DOWN = 108
KEY_RIGHTMETA = 126
KEY_COMPOSE = 127
KEY_MENU = 139
KEY_CONTEXT_MENU = 0x1B6
KEY_MAX = 0x2FF

KEY_NAMES = {
    "capslock": KEY_CAPSLOCK,
    "slash": KEY_SLASH,
    "rightctrl": KEY_RIGHTCTRL,
    "rightalt": KEY_RIGHTALT,
    "up": KEY_UP,
    "left": KEY_LEFT,
    "right": KEY_RIGHT,
    "down": KEY_DOWN,
    "menu": KEY_MENU,
    "compose": KEY_COMPOSE,
    "rightmeta": KEY_RIGHTMETA,
    "contextmenu": KEY_CONTEXT_MENU,
}
KEY_ALIASES = {
    "caps": KEY_CAPSLOCK,
    "caps_lock": KEY_CAPSLOCK,
    "slash_question": KEY_SLASH,
    "question": KEY_SLASH,
    "right_alt": KEY_RIGHTALT,
    "ralt": KEY_RIGHTALT,
    "right_ctrl": KEY_RIGHTCTRL,
    "rctrl": KEY_RIGHTCTRL,
    "arrow_up": KEY_UP,
    "arrow_left": KEY_LEFT,
    "arrow_right": KEY_RIGHT,
    "arrow_down": KEY_DOWN,
}
KEY_LABELS = {
    KEY_CAPSLOCK: "CapsLock",
    KEY_SLASH: "/ ?",
    KEY_RIGHTALT: "Right Alt",
    KEY_RIGHTCTRL: "Right Ctrl",
    KEY_MENU: "Menu (139)",
    KEY_COMPOSE: "Menu (Compose)",
    KEY_RIGHTMETA: "Right Meta",
    KEY_CONTEXT_MENU: "Context Menu",
    KEY_UP: "Up",
    KEY_LEFT: "Left",
    KEY_DOWN: "Down",
    KEY_RIGHT: "Right",
}

DEFAULT_DEVICE = "/dev/input/by-id/usb-BY_Tech_KEMOVE_K61-event-kbd"

# _IOC helpers: x86_64/Linux values, matching <linux/uinput.h>.
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(direction, type_, nr, size):
    return (
        (direction << _IOC_DIRSHIFT)
        | ((type_ & 0xFF) << _IOC_TYPESHIFT)
        | ((nr & 0xFF) << _IOC_NRSHIFT)
        | ((size & 0x3FFF) << _IOC_SIZESHIFT)
    )


def _IOW(type_, nr, size):
    return _IOC(_IOC_WRITE, type_, nr, size)


def _IO(type_, nr):
    return _IOC(0, type_, nr, 0)


EVIOCGRAB = _IOW(ord("E"), 0x90, 4)
UI_SET_EVBIT = _IOW(ord("U"), 100, 4)
UI_SET_KEYBIT = _IOW(ord("U"), 101, 4)
UI_DEV_CREATE = _IO(ord("U"), 1)
UI_DEV_DESTROY = _IO(ord("U"), 2)
UI_DEV_SETUP = _IOW(ord("U"), 3, 92)

EVENT = struct.Struct("llHHi")
EVENT_SIZE = EVENT.size
UINPUT_SETUP = struct.Struct("HHHH80sI")


def parse_key(text: str) -> int:
    normalized = text.strip().lower().replace("-", "_")
    normalized = normalized.removeprefix("key_")
    compact = normalized.replace("_", "")
    if normalized in KEY_NAMES:
        return KEY_NAMES[normalized]
    if normalized in KEY_ALIASES:
        return KEY_ALIASES[normalized]
    if compact in KEY_NAMES:
        return KEY_NAMES[compact]
    if compact in KEY_ALIASES:
        return KEY_ALIASES[compact]
    try:
        value = int(text, 0)
    except ValueError as exc:
        raise ValueError(f"unknown key {text!r}") from exc
    if not 0 <= value <= KEY_MAX:
        raise ValueError(f"key code must be 0..0x{KEY_MAX:X}")
    return value


def find_device(path=None):
    if path:
        return path
    if os.path.exists(DEFAULT_DEVICE):
        return DEFAULT_DEVICE
    matches = sorted(glob.glob("/dev/input/by-id/*KEMOVE*K61*event-kbd"))
    if matches:
        return matches[0]
    raise RuntimeError(
        "K61SE keyboard event node not found; pass --device "
        "/dev/input/by-id/...-event-kbd"
    )


def emit_event(fd, type_, code, value):
    now = time.time()
    sec = int(now)
    usec = int((now - sec) * 1_000_000)
    os.write(fd, EVENT.pack(sec, usec, type_, code, value))


class ArrowLayer:
    def __init__(self, device, mapping, hotkey=KEY_CAPSLOCK,
                 window_ms=300, single_tap="hotkey", verbose=False):
        self.device = device
        self.mapping = mapping
        self.hotkey = hotkey
        self.window = window_ms / 1000.0
        self.single_tap = single_tap
        self.verbose = verbose
        self.ev_fd = None
        self.ui_fd = None
        self.arrow_mode = False
        self.caps_pending = None
        self.down_map = {}
        self.running = True

    # ------------------------------------------------------------------ #
    # uinput
    # ------------------------------------------------------------------ #
    def open_uinput(self):
        try:
            self.ui_fd = os.open("/dev/uinput", os.O_WRONLY)
        except OSError as exc:
            raise RuntimeError(f"cannot open /dev/uinput: {exc}") from exc

        for event_type in (EV_SYN, EV_KEY, EV_REP):
            fcntl.ioctl(self.ui_fd, UI_SET_EVBIT, event_type)
        for code in range(KEY_MAX + 1):
            fcntl.ioctl(self.ui_fd, UI_SET_KEYBIT, code)

        setup = UINPUT_SETUP.pack(
            0x03, 0x258A, 0x010C, 0x0001,
            b"K61SE Arrow Layer"[:80].ljust(80, b"\0"), 0,
        )
        fcntl.ioctl(self.ui_fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.ui_fd, UI_DEV_CREATE)
        time.sleep(0.10)

    def close(self):
        if self.ui_fd is not None:
            try:
                fcntl.ioctl(self.ui_fd, UI_DEV_DESTROY)
            except OSError:
                pass
            os.close(self.ui_fd)
            self.ui_fd = None
        if self.ev_fd is not None:
            try:
                fcntl.ioctl(self.ev_fd, EVIOCGRAB, 0)
            except OSError:
                pass
            os.close(self.ev_fd)
            self.ev_fd = None

    # ------------------------------------------------------------------ #
    # output
    # ------------------------------------------------------------------ #
    def emit(self, type_, code, value):
        if self.ui_fd is not None:
            emit_event(self.ui_fd, type_, code, value)

    def syn(self):
        self.emit(EV_SYN, SYN_REPORT, 0)

    def tap_hotkey(self):
        self.emit(EV_KEY, self.hotkey, 1)
        self.emit(EV_KEY, self.hotkey, 0)
        self.syn()

    def toggle(self):
        self.arrow_mode = not self.arrow_mode
        state = "ON" if self.arrow_mode else "OFF"
        print(f"[arrows] sticky arrow layer {state}", flush=True)

    def flush_caps_pending(self):
        if self.caps_pending is None:
            return
        self.caps_pending = None
        if self.single_tap == "hotkey":
            self.tap_hotkey()

    # ------------------------------------------------------------------ #
    # key handling
    # ------------------------------------------------------------------ #
    def caps_press(self, now):
        if self.caps_pending is not None:
            if now - self.caps_pending <= self.window:
                self.caps_pending = None
                self.toggle()
                return
            self.flush_caps_pending()
        self.caps_pending = now

    def handle_key(self, code, value, now):
        if code == self.hotkey:
            if value == 1:
                self.caps_press(now)
            # key-up and repeat are suppressed
            return

        # A normal key means the user is no longer doing a double-tap.
        if self.caps_pending is not None:
            self.flush_caps_pending()

        if value == 2:
            return

        if value == 1:
            if code in self.down_map:
                return
            mapped = self.mapping.get(code) if self.arrow_mode else None
            emitted = mapped if mapped is not None else code
            self.down_map[code] = emitted
            self.emit(EV_KEY, emitted, 1)
        elif value == 0:
            emitted = self.down_map.pop(code, code)
            self.emit(EV_KEY, emitted, 0)

    def handle_event(self, type_, code, value, now):
        if self.verbose:
            print(f"  event type={type_} code={code} value={value}", flush=True)
        if type_ == EV_KEY:
            self.handle_key(code, value, now)
        elif type_ == EV_SYN:
            self.syn()

    # ------------------------------------------------------------------ #
    # main loop
    # ------------------------------------------------------------------ #
    def run(self):
        # Create the virtual keyboard before grabbing the real one, so a
        # uinput failure never leaves the physical keyboard grabbed.
        try:
            self.open_uinput()
        except Exception:
            self.close()
            raise

        try:
            self.ev_fd = os.open(self.device, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            self.close()
            raise RuntimeError(f"cannot open {self.device}: {exc}") from exc

        try:
            fcntl.ioctl(self.ev_fd, EVIOCGRAB, 1)
        except OSError as exc:
            self.close()
            raise RuntimeError(f"cannot grab {self.device}: {exc}") from exc

        print(
            f"[arrows] listening on {self.device}; double-tap CapsLock to "
            f"toggle. Ctrl-C quits.",
            flush=True,
        )
        try:
            while self.running:
                # Keep a short maximum timeout so SIGINT is handled even when
                # no key is pressed.  If a CapsLock tap is pending, wake exactly
                # when its double-tap window expires.
                timeout = 0.20
                if self.caps_pending is not None:
                    timeout = min(
                        timeout,
                        max(0.0, self.caps_pending + self.window - time.monotonic()),
                    )
                readable, _, _ = select.select([self.ev_fd], [], [], timeout)
                if readable:
                    try:
                        data = os.read(self.ev_fd, EVENT_SIZE * 64)
                    except BlockingIOError:
                        data = b""
                    now = time.monotonic()
                    for offset in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                        _sec, _usec, type_, code, value = EVENT.unpack_from(
                            data, offset
                        )
                        self.handle_event(type_, code, value, now)
                if self.caps_pending is not None:
                    if time.monotonic() >= self.caps_pending + self.window:
                        self.flush_caps_pending()
        finally:
            self.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description="Sticky arrow layer for the K61SE using evdev grab + "
                    "uinput. Double-tap CapsLock toggles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:", 1)[1] if "Examples:" in __doc__ else None,
    )
    parser.add_argument("--device", help=f"keyboard event node (default: {DEFAULT_DEVICE})")
    parser.add_argument("--hotkey", default="capslock",
                        help="toggle key name or code (default: capslock)")
    parser.add_argument("--up", default="slash",
                        help="physical key for Up in arrow mode (default: slash)")
    parser.add_argument("--left", default="rightalt",
                        help="physical key for Left (default: rightalt)")
    parser.add_argument("--down", default="compose",
                        help="physical key for Down (default: compose; the K61SE "
                             "Menu key is KEY_COMPOSE 127 on this kernel)")
    parser.add_argument("--right", default="rightctrl",
                        help="physical key for Right (default: rightctrl)")
    parser.add_argument("--window-ms", type=int, default=300,
                        help="double-tap window in ms (default: 300)")
    parser.add_argument("--single-tap", choices=("hotkey", "none"),
                        default="hotkey",
                        help="what a single hotkey tap does (default: emit it)")
    parser.add_argument("--verbose", action="store_true",
                        help="print input events")
    parser.add_argument("--print-mapping", action="store_true",
                        help="show the resolved mapping and exit")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.window_ms < 100 or args.window_ms > 1000:
        parser.error("--window-ms must be 100..1000")
    try:
        hotkey = parse_key(args.hotkey)
        mapping = {
            parse_key(args.up): KEY_UP,
            parse_key(args.left): KEY_LEFT,
            parse_key(args.down): KEY_DOWN,
            parse_key(args.right): KEY_RIGHT,
        }
    except ValueError as exc:
        parser.error(str(exc))

    if hotkey in mapping:
        parser.error("the hotkey is also one of the arrow keys")
    if len(set(mapping)) != 4:
        parser.error("the four arrow source keys must be distinct")

    def label(code):
        return KEY_LABELS.get(code, f"code {code}")

    print("K61SE sticky arrow layer:")
    print(f"  hotkey:       {label(hotkey)} (double-tap)")
    print(f"  window:       {args.window_ms} ms")
    print(f"  single tap:   {args.single_tap}")
    for source, target in mapping.items():
        print(f"  {label(source):<12} -> {label(target)}")
    if args.print_mapping:
        return 0

    try:
        device = find_device(args.device)
        layer = ArrowLayer(
            device, mapping, hotkey=hotkey, window_ms=args.window_ms,
            single_tap=args.single_tap, verbose=args.verbose,
        )
        signal.signal(signal.SIGINT, lambda *_: setattr(layer, "running", False))
        signal.signal(signal.SIGTERM, lambda *_: setattr(layer, "running", False))
        layer.run()
    except (RuntimeError, OSError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        print(
            "    Run with sudo for the first test, or grant read access to the "
            "K61SE event node and write access to /dev/uinput.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
