#!/usr/bin/env python3
"""Read K61SE key presses from its standard boot-keyboard interface.

The K61SE is a two-interface HID device:

  * interface 0 - ordinary boot keyboard (usbhid, evdev + hidraw)
  * interface 1 - vendor lighting/music control (usbfs, report 6)

The vendor interface carries the 520-byte lighting protocol.  Key presses do
**not** need it: interface 0 exposes the normal 8-byte HID keyboard report,
and its report descriptor is the standard boot-keyboard one:

    05 01 09 06 a1 01              Usage Page (Generic Desktop), Usage (Keyboard)
    05 07 19 e0 29 e7 ...          modifier bits, 8 x 1
    95 06 75 08 15 00 26 ff 00 ... six 8-bit keycodes

So each report is:

    byte 0      modifier bitmap (left/right Ctrl, Shift, Alt, GUI)
    byte 1      reserved
    bytes 2..7  up to six HID keyboard usage IDs

The app's XML already stores the exact HID usage code for every key, so the
mapping from a report to a `key_index` is direct.  No evdev permission is
needed: /dev/hidraw* for the keyboard is normally readable by the desktop user.

This tool is **read-only**.  It never writes to the keyboard and is safe to run
while the vendor app or another lighting writer is active.

    python3 k61se_input.py                 # until Ctrl-C
    python3 k61se_input.py --raw           # also show the 8-byte report
    python3 k61se_input.py --seconds 30
    python3 k61se_input.py --list-keys     # XML code -> key_index mapping
    python3 k61se_input.py --device /dev/hidraw4

Fn is handled inside the keyboard firmware and may not appear in this standard
keyboard report.  Ordinary keys and all modifiers do.
"""
import argparse
import glob
import os
import select
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

VENDOR_ID = "258A"
PRODUCT_ID = "010C"
KEYMAP_XML = Path(__file__).resolve().parent / "app" / "K61SE-keyboard.xml"
KEYBOARD_DESCRIPTOR_PREFIX = bytes.fromhex("05010906A101")


def load_keymap():
    """Return {HID usage code: (key_index, name, row_col)} from the XML."""
    if not KEYMAP_XML.exists():
        raise RuntimeError(f"missing key map: {KEYMAP_XML}")
    root = ET.parse(KEYMAP_XML).getroot()
    mapping = {}
    for element in root.iter("key"):
        code_text = element.get("code")
        index_text = element.get("key_index")
        if not code_text or not index_text:
            continue
        try:
            code = int(code_text, 0)
            index = int(index_text)
        except ValueError:
            continue
        mapping[code] = (
            index,
            element.get("name", "?"),
            element.get("row_col", ""),
        )
    return mapping


def find_keyboard_node():
    """Find the K61SE interface-0 hidraw node, even after a replug."""
    candidates = []
    for uevent in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        try:
            with open(uevent) as handle:
                text = handle.read()
        except OSError:
            continue
        upper = text.upper()
        if VENDOR_ID not in upper or PRODUCT_ID not in upper:
            continue

        device_dir = os.path.dirname(uevent)
        descriptor_path = os.path.join(device_dir, "report_descriptor")
        try:
            with open(descriptor_path, "rb") as handle:
                descriptor = handle.read()
        except OSError:
            continue
        if not descriptor.startswith(KEYBOARD_DESCRIPTOR_PREFIX):
            continue

        # /sys/class/hidraw/hidraw4/device/uevent -> /dev/hidraw4
        hidraw_dir = os.path.dirname(device_dir)
        node = os.path.join("/dev", os.path.basename(hidraw_dir))
        candidates.append((len(descriptor), node))

    if not candidates:
        raise RuntimeError(
            "no K61SE boot-keyboard hidraw node found; is the keyboard "
            "connected? use --device /dev/hidrawN to override"
        )
    return sorted(candidates)[0][1]


def decode_report(report, keymap=None):
    """Turn one boot-keyboard report into a set of HID usage codes.

    Returns None for a short/empty report.  `keymap` is accepted for callers
    that want the same signature, but decoding itself only needs the bytes.
    """
    if len(report) >= 9 and report[0] in (0x00, 0x01):
        # A report-id-prefixed report; the standard descriptor has no report
        # ID, but accepting one costs nothing and makes captures easier.
        report = report[1:9]
    elif len(report) >= 8:
        report = report[:8]
    else:
        return None

    modifiers = report[0]
    codes = {0xE0 + bit for bit in range(8) if modifiers & (1 << bit)}
    codes.update(code for code in report[2:8] if code)
    return codes


def describe_code(code, keymap, unknown="?"):
    item = keymap.get(code)
    if item is None:
        return f"{unknown} key_index={unknown} row={unknown} code=0x{code:02X}"
    index, name, row_col = item
    return f"{name:<9} key_index={index:<3} row={row_col:<4} code=0x{code:02X}"


def list_keys(keymap):
    for code, (index, name, row_col) in sorted(
            keymap.items(), key=lambda item: item[1][0]):
        print(f"key_index={index:<3} row={row_col:<4} "
              f"code=0x{code:02X}  {name}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read K61SE key presses (read-only; no lighting writes).")
    parser.add_argument("--device", help="/dev/hidrawN override")
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="stop after N seconds; 0 = until Ctrl-C")
    parser.add_argument("--raw", action="store_true",
                        help="print each raw 8-byte keyboard report")
    parser.add_argument("--list-keys", action="store_true",
                        help="print the XML HID-code -> key_index mapping")
    opts = parser.parse_args(argv)

    try:
        keymap = load_keymap()
    except (OSError, RuntimeError, ET.ParseError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1

    if opts.list_keys:
        list_keys(keymap)
        return 0

    try:
        node = opts.device or find_keyboard_node()
    except RuntimeError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1

    try:
        fd = os.open(node, os.O_RDONLY)
    except PermissionError:
        print(f"[!] Permission denied opening {node}.", file=sys.stderr)
        print("    The hidraw node needs read access for your user; check "
              "udev/ACL or run with appropriate group access.", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[!] Cannot open {node}: {exc}", file=sys.stderr)
        return 1

    print(f"[+] listening on {node}; Ctrl-C to stop", file=sys.stderr)
    pressed = set()
    deadline = time.monotonic() + opts.seconds if opts.seconds > 0 else None
    try:
        while True:
            timeout = 0.5
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                timeout = min(timeout, remaining)

            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                continue
            data = os.read(fd, 64)
            if not data:
                break
            if opts.raw:
                print("raw " + " ".join(f"{byte:02X}" for byte in data),
                      flush=True)

            codes = decode_report(data)
            if codes is None:
                continue

            for code in sorted(codes - pressed):
                print("press   " + describe_code(code, keymap), flush=True)
            for code in sorted(pressed - codes):
                print("release " + describe_code(code, keymap), flush=True)
            pressed = codes
    except KeyboardInterrupt:
        print(file=sys.stderr)
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
