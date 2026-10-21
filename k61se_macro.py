#!/usr/bin/env python3
"""Read-only keymap and macro inspector for the KEMOVE K61SE.

This is deliberately a **read-only** tool.  The keymap write/read pair is now
hardware-confirmed and lives in ``k61se_keymap.py``.  One F75-style macro payload
has been written and read back in ``k61se_macro_write.py``; playback and key
binding are still being tested.  This program itself cannot write any config
block.

Commands copied from the wired CDevG5KB methods in ``app/KBAccess.dll`` (the
same command family as the AULA F75 wired protocol):

    keymap write 0x03 / read 0x83   body 0x1F8 bytes (126 x 4), sub = layer
    macro  write 0x05 / read 0x85   variable length, sub = 0
    realtime     0x08              body 0x17A bytes, sub = 0

Keymap body layout: one 4-byte action per matrix cell, at
``4 * key_index``.  The XML's ``key_index`` is the keymap cell number directly
(Esc is 1, Tab 2, ...); slot 0 is reserved.  An older app readback had a
duplicate Esc there; the Fn+Tab reset snapshot leaves it zero.  Action formats
are the same as the F75:

    [0x00, modifier_bits, 0x00, HID_usage]   keyboard key
    [0x02, 0x00, usage_hi, usage_lo]         consumer key
    [0x03, mode, count, macro_index]         macro key (index is 0-based)

Macro body: ``N * [addr u16, size u16]`` followed by N blobs.  Each blob is
``name_len u8, UTF-16LE name, events...`` and each event is four bytes:

    [(type << 4) | up << 7 | delay[19:16], delay[15:8], delay[7:0], code]

Examples (the first two need no keyboard):

    python3 k61se_macro.py --decode-keymap dumps/keymap-layer0.bin
    python3 k61se_macro.py --decode-macros dumps/macros-512.bin
    python3 k61se_macro.py --probe
    python3 k61se_macro.py --probe --layers 0,1,2,3 --macro-size 512 --save
    python3 k61se_macro.py --echo-test

The probe and echo test only send read requests (the op byte with bit 7 set).
They never write a lighting, colour, keymap, or macro block.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import k61se_dev as dev

KEYMAP_OP = 0x03
MACRO_OP = 0x05
INFO_OP = 0x02
KEYMAP_SIZE = 126 * 4                 # 0x1F8, same as KBAccess SetKey
MATRIX_CELLS = 126
XML_PATH = Path(__file__).resolve().parent / "app" / "K61SE-keyboard.xml"

# HID keyboard usage page (0x07), usages 0x04..0x65.  Enough for the K61SE and
# for readable macro dumps; unknown values are printed numerically.
HID_NAMES = {
    0x04: "A", 0x05: "B", 0x06: "C", 0x07: "D", 0x08: "E", 0x09: "F",
    0x0A: "G", 0x0B: "H", 0x0C: "I", 0x0D: "J", 0x0E: "K", 0x0F: "L",
    0x10: "M", 0x11: "N", 0x12: "O", 0x13: "P", 0x14: "Q", 0x15: "R",
    0x16: "S", 0x17: "T", 0x18: "U", 0x19: "V", 0x1A: "W", 0x1B: "X",
    0x1C: "Y", 0x1D: "Z",
    0x1E: "1", 0x1F: "2", 0x20: "3", 0x21: "4", 0x22: "5",
    0x23: "6", 0x24: "7", 0x25: "8", 0x26: "9", 0x27: "0",
    0x28: "Enter", 0x29: "Esc", 0x2A: "Backspace", 0x2B: "Tab",
    0x2C: "Space", 0x2D: "-", 0x2E: "=", 0x2F: "[", 0x30: "]",
    0x31: "\\", 0x32: "Non-US #", 0x33: ";", 0x34: "'", 0x35: "`",
    0x36: ",", 0x37: ".", 0x38: "/", 0x39: "Caps Lock",
    0x3A: "F1", 0x3B: "F2", 0x3C: "F3", 0x3D: "F4", 0x3E: "F5",
    0x3F: "F6", 0x40: "F7", 0x41: "F8", 0x42: "F9", 0x43: "F10",
    0x44: "F11", 0x45: "F12",
    0x46: "Print Screen", 0x47: "Scroll Lock", 0x48: "Pause",
    0x49: "Insert", 0x4A: "Home", 0x4B: "Page Up", 0x4C: "Delete",
    0x4D: "End", 0x4E: "Page Down", 0x4F: "Right", 0x50: "Left",
    0x51: "Down", 0x52: "Up", 0x53: "Num Lock", 0x54: "KP /",
    0x55: "KP *", 0x56: "KP -", 0x57: "KP +", 0x58: "KP Enter",
    0x59: "KP 1", 0x5A: "KP 2", 0x5B: "KP 3", 0x5C: "KP 4",
    0x5D: "KP 5", 0x5E: "KP 6", 0x5F: "KP 7", 0x60: "KP 8",
    0x61: "KP 9", 0x62: "KP 0", 0x63: "KP .", 0x64: "Non-US \\",
    0x65: "Menu", 0x66: "Power", 0x67: "KP =",
    0x68: "F13", 0x69: "F14", 0x6A: "F15", 0x6B: "F16",
    0x6C: "F17", 0x6D: "F18", 0x6E: "F19", 0x6F: "F20",
    0x70: "F21", 0x71: "F22", 0x72: "F23", 0x73: "F24",
    0xE0: "Left Ctrl", 0xE1: "Left Shift", 0xE2: "Left Alt",
    0xE3: "Left Win", 0xE4: "Right Ctrl", 0xE5: "Right Shift",
    0xE6: "Right Alt", 0xE7: "Right Win",
}

CONSUMER_NAMES = {
    0x00CD: "Play/Pause", 0x00B5: "Next Track", 0x00B6: "Previous Track",
    0x00B7: "Stop", 0x00E2: "Mute", 0x00E9: "Volume Up",
    0x00EA: "Volume Down", 0x0183: "Calculator", 0x018A: "Mail",
    0x0192: "Browser", 0x0194: "Back", 0x0196: "Forward",
    0x0197: "Stop Browser", 0x0198: "Refresh", 0x0221: "Search",
    0x0223: "Home", 0x0224: "Back", 0x0225: "Forward",
}

MODIFIER_BITS = {
    0x01: "Left Ctrl", 0x02: "Left Shift", 0x04: "Left Alt", 0x08: "Left Win",
    0x10: "Right Ctrl", 0x20: "Right Shift", 0x40: "Right Alt", 0x80: "Right Win",
}

MACRO_MODES = {1: "play once", 2: "hold/repeat", 4: "toggle"}
EVENT_TYPES = {0: "key", 1: "modifier", 2: "mouse button", 3: "mouse X",
               4: "mouse Y", 5: "wheel"}


# --------------------------------------------------------------------------- #
# XML layout
# --------------------------------------------------------------------------- #
def load_xml_keys(path: Path = XML_PATH):
    """Return {key_index: [key dicts]} from the vendor XML.

    ``key_index`` is the 1-based keymap cell number and is used directly as the
    cell index: the wire offset is ``4 * key_index``.  Slot 0 is reserved; the
    older app readback had a duplicate Esc there, which is why this is not
    ``key_index - 1``.  Duplicate labels (Shift/Ctrl/Alt) are preserved as
    multiple entries.
    """
    if not path.exists():
        raise RuntimeError(f"missing key map: {path}")
    root = ET.parse(path).getroot()
    keys: dict[int, list[dict]] = {}
    for elem in root.iter("key"):
        try:
            key_index = int(elem.get("key_index"))
        except (TypeError, ValueError):
            continue
        idx = key_index
        if not 0 <= idx < MATRIX_CELLS:
            continue
        try:
            code = int(elem.get("code", "0"), 0)
        except ValueError:
            code = 0
        keys.setdefault(idx, []).append(
            {
                "name": elem.get("name", "?"),
                "desc": elem.get("desc", ""),
                "code": code,
                "row_col": elem.get("row_col", ""),
            }
        )
    return keys


# --------------------------------------------------------------------------- #
# action decoding
# --------------------------------------------------------------------------- #
def usage_name(usage: int) -> str:
    return HID_NAMES.get(usage, f"usage 0x{usage:02X}")


def consumer_name(usage: int) -> str:
    return CONSUMER_NAMES.get(usage, f"consumer 0x{usage:03X}")


def describe_code(code: bytes) -> str:
    """Human-readable action for one 4-byte keymap cell."""
    if len(code) != 4:
        return "short"
    c = bytes(code)
    if c == b"\x00\x00\x00\x00":
        return "disabled"
    # KBAccess's SetKey maps the vendor's Fn codes 0xFA/0xFB to these.
    if c == b"\x0d\x00\x00\x00":
        return "Fn (vendor special)"
    if c == b"\x0d\x01\x00\x00":
        return "Fn2 (vendor special)"
    kind = c[0]
    if kind == 0:
        mods, usage = c[1], c[3]
        if mods and usage == 0:
            names = [name for bit, name in MODIFIER_BITS.items() if mods & bit]
            extra = mods & ~sum(MODIFIER_BITS)
            text = " + ".join(names) if names else "modifier"
            if extra:
                text += f" + bits 0x{extra:02X}"
            return text
        if usage:
            suffix = f" + mods 0x{mods:02X}" if mods else ""
            return f"key {usage_name(usage)}{suffix}"
        return f"keyboard mods=0x{mods:02X} usage=0x00"
    if kind == 2:
        usage = (c[2] << 8) | c[3]
        return f"consumer {consumer_name(usage)} (0x{usage:03X})"
    if kind == 3:
        mode = MACRO_MODES.get(c[1], f"mode {c[1]}")
        return f"macro {c[3] + 1} ({mode}, count {c[2]})"
    if kind == 1:
        return f"vendor/media action (type 1): {c.hex(' ')}"
    return f"unknown {c.hex(' ')}"


def expected_xml_code(key: dict) -> bytes | None:
    """The factory matrix code implied by the XML, when it is unambiguous."""
    usage = key["code"] & 0xFF
    name = key["name"].lower()
    if name == "fn" or usage == 0xAF:
        return None                       # vendor-specific Fn encoding
    if 0xE0 <= usage <= 0xE7:             # XML gives a HID modifier usage
        return bytes([0, 1 << (usage - 0xE0), 0, 0])
    if usage:
        return bytes([0, 0, 0, usage])
    return None


def keymap_report(body: bytes, keys: dict[int, list[dict]]):
    """Decode a 504-byte keymap body and score how much it looks like a keymap."""
    cells = []
    for idx in range((len(body) // 4) if len(body) >= 4 else 0):
        code = bytes(body[4 * idx:4 * idx + 4])
        cells.append(
            {
                "index": idx,
                "label": (
                    "slot 0 (reserved)"
                    if idx == 0
                    else " / ".join(k["name"] for k in keys.get(idx, []))
                ),
                "code": code,
                "summary": describe_code(code),
            }
        )

    # key_index 1 is the physical Esc.  Slot 0 is reserved; the older app
    # readback had a duplicate Esc there, but the reset snapshot leaves it zero.
    esc_ok = len(body) >= 8 and bytes(body[4:8]) == b"\x00\x00\x00\x29"
    checked = matched = 0
    for idx, entries in keys.items():
        if idx >= len(cells):
            continue
        for key in entries:
            want = expected_xml_code(key)
            if want is None:
                continue
            checked += 1
            if cells[idx]["code"] == want:
                matched += 1
    plausible = sum(
        1 for c in cells if c["code"][0] in (0, 2, 3) and c["code"] != b"\x00\x00\x00\x00"
    )
    return {
        "cells": cells,
        "esc_ok": esc_ok,
        "checked": checked,
        "matched": matched,
        "plausible": plausible,
        "looks_like_keymap": esc_ok and (checked == 0 or matched * 10 >= checked * 7),
    }


def _first_physical(cells, keys):
    return [cell for cell in cells if cell["index"] in keys]


def print_keymap(body: bytes, keys, layer: int, full: bool = False):
    report = keymap_report(body, keys)
    print(f"\nKeymap layer {layer}: {len(body)} bytes, {len(report['cells'])} cells")
    print(
        "  Esc(1)=%s, factory XML matches %d/%d, plausible cells %d, looks like keymap: %s"
        % (
            "OK" if report["esc_ok"] else "not 00 00 00 29",
            report["matched"],
            report["checked"],
            report["plausible"],
            "yes" if report["looks_like_keymap"] else "unknown",
        )
    )
    shown = report["cells"] if full else _first_physical(report["cells"], keys)
    if not full:
        extra = [
            c for c in report["cells"]
            if c["index"] not in keys and c["code"] != b"\x00\x00\x00\x00"
        ]
        shown = shown + extra
    for cell in shown:
        label = f" {cell['label']:<10}" if cell["label"] else "           "
        print(f"  {cell['index']:>3}{label} {cell['code'].hex(' ')}  {cell['summary']}")
    if not full:
        shown_idx = {cell["index"] for cell in shown}
        hidden_zero = sum(
            1 for cell in report["cells"]
            if cell["index"] not in shown_idx and cell["code"] == b"\x00\x00\x00\x00"
        )
        if hidden_zero:
            print(f"  ... plus {hidden_zero} all-zero cells not shown")
    return report


# --------------------------------------------------------------------------- #
# macro decoding
# --------------------------------------------------------------------------- #
def _u16(buf: bytes, off: int) -> int:
    return buf[off] | (buf[off + 1] << 8)


def _macro_table(buf: bytes):
    """Find the N x [addr, size] table.

    Returns ``(n, entries, end, truncated)``.  ``end`` is where the last blob
    would end, even if the caller only read part of the macro area, so a short
    read can still show the table and the macros that fit.
    """
    if len(buf) < 4:
        return 0, [], 0, False
    first = _u16(buf, 0)
    if first == 0 or first == 0xFFFF:
        return 0, [], 0, False
    # The first blob starts immediately after the table, so N = first / 4.
    if first % 4:
        return 0, [], 0, False
    n = first // 4
    if not 1 <= n <= min(100, len(buf) // 4):
        return 0, [], 0, False
    entries = []
    expected = first
    truncated = False
    for i in range(n):
        off = i * 4
        addr, size = _u16(buf, off), _u16(buf, off + 2)
        if addr != expected or size == 0:
            return 0, [], 0, False
        if addr + size > len(buf):
            truncated = True
        entries.append((addr, size))
        expected += size
    return n, entries, expected, truncated


def _event_score(blob: bytes, off: int) -> int:
    """Prefer the name-length interpretation whose events look like events."""
    if off > len(blob):
        return -1000
    rem = blob[off:]
    if len(rem) % 4:
        return -100
    score = 0
    for i in range(0, len(rem), 4):
        head, _, _, code = rem[i:i + 4]
        kind = (head >> 4) & 0x07
        if kind > 5:
            score -= 6
        elif kind == 1:
            score += 2 if 0xE0 <= code <= 0xE7 else -2
        elif kind == 2:
            score += 1 if code in (1, 2, 4, 8, 16) else -2
        else:
            score += 1
    return score


def _decode_name(blob: bytes, name_len: int):
    """Return (name, event_offset, encoding) for the best name interpretation."""
    if name_len == 0:
        return "", 1, "empty"
    options = []
    if 1 + name_len <= len(blob):
        raw = blob[1:1 + name_len]
        if len(raw) % 2 == 0:
            options.append(
                (raw.decode("utf-16le", "replace"), 1 + name_len, "bytes")
            )
    if 1 + 2 * name_len <= len(blob):
        raw = blob[1:1 + 2 * name_len]
        options.append(
            (raw.decode("utf-16le", "replace"), 1 + 2 * name_len, "chars")
        )
    if not options:
        return "", min(1 + name_len, len(blob)), "truncated"
    # F75 stores the byte length; KBAccess's AddMacro looks as if it stores a
    # character count.  Score the event stream and prefer the byte form on ties.
    scored = [(_event_score(blob, off), -i, name, off, enc)
              for i, (name, off, enc) in enumerate(options)]
    scored.sort(reverse=True)
    _, _, name, off, enc = scored[0]
    return name, off, enc


def decode_events(blob: bytes, off: int):
    events = []
    for pos in range(off, len(blob) - 3, 4):
        head, d1, d2, code = blob[pos:pos + 4]
        if head == 0 and d1 == 0 and d2 == 0 and code == 0:
            break
        kind = (head >> 4) & 0x07
        up = bool(head & 0x80)
        delay = ((head & 0x0F) << 16) | (d1 << 8) | d2
        if kind == 0:
            what = f"key {usage_name(code)}"
        elif kind == 1:
            what = f"modifier {usage_name(code) if 0xE0 <= code <= 0xE7 else hex(code)}"
        elif kind == 2:
            what = f"mouse button 0x{code:02X}"
        elif kind == 3:
            what = f"mouse X {code}"
        elif kind == 4:
            what = f"mouse Y {code}"
        elif kind == 5:
            what = f"wheel {code}"
        else:
            what = f"type {kind} code 0x{code:02X}"
        events.append(
            {
                "raw": bytes([head, d1, d2, code]),
                "type": kind,
                "type_name": EVENT_TYPES.get(kind, f"unknown {kind}"),
                "up": up,
                "delay_ms": delay,
                "code": code,
                "summary": f"{'up  ' if up else 'down'} {what} +{delay}ms",
            }
        )
    return events


def parse_macros(buf: bytes):
    n, table, end, truncated = _macro_table(buf)
    if n == 0:
        return {"count": 0, "table": [], "used": 0, "truncated": False,
                "macros": []}
    macros = []
    for i, (addr, size) in enumerate(table):
        blob = buf[addr:min(addr + size, len(buf))]
        if not blob:
            macros.append({"index": i, "addr": addr, "size": size, "name": "",
                           "events": [], "error": "not read"})
            continue
        name, event_off, enc = _decode_name(blob, blob[0])
        macros.append(
            {
                "index": i,
                "addr": addr,
                "size": size,
                "name_len_byte": blob[0],
                "name_encoding": enc,
                "name": name,
                "events": decode_events(blob, event_off),
                "truncated": addr + size > len(buf),
            }
        )
    return {"count": n, "table": table, "used": end,
            "truncated": truncated, "macros": macros}


def print_macros(buf: bytes):
    parsed = parse_macros(buf)
    print(f"\nMacro area: {len(buf)} bytes")
    if not parsed["count"]:
        print("  no macro table (first word is 0 or not a valid 4-byte table)")
        return parsed
    print(
        f"  {parsed['count']} macro(s), table + blobs use {parsed['used']} bytes"
    )
    if parsed["truncated"]:
        print("  [!] blob data is truncated: increase --macro-size "
              "(max 4096) for the complete area")
    for m in parsed["macros"]:
        name = m.get("name") or "(unnamed)"
        print(
            f"\n  Macro {m['index'] + 1}: name={name!r} "
            f"addr=0x{m['addr']:03X} size={m['size']} "
            f"name_len={m.get('name_len_byte')} ({m.get('name_encoding')})"
        )
        if m.get("error"):
            print(f"    error: {m['error']}")
        if m.get("truncated"):
            print("    [!] this macro extends past the bytes read")
        for ev in m["events"]:
            print(f"    {ev['raw'].hex(' ')}  {ev['summary']}")
        if not m["events"]:
            print("    (no events)")
    return parsed


# --------------------------------------------------------------------------- #
# device probe (read-only)
# --------------------------------------------------------------------------- #
def _read(handle, opcode, size, sub=0):
    return bytes(dev.read_body(opcode, size, sub, dev=handle))


def _read_chunks(handle, opcode, size, sub=0):
    return bytes(dev.read_body_chunks(opcode, size, sub, dev=handle))


def _parse_layers(text):
    if text.strip().lower() == "all":
        return list(range(8))
    layers = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        value = int(part, 0)
        if not 0 <= value <= 7:
            raise argparse.ArgumentTypeError("layers must be 0..7")
        layers.append(value)
    return layers or [0, 1]


def _save(save_dir: str, name: str, data: bytes):
    directory = Path(save_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(data)
    return path


def echo_test(args):
    """Check whether 0x83/0x85 actually answer, or just echo the request.

    A read request normally has a zero-filled payload, so an all-zero body is
    ambiguous: it can be a real zero-filled block, or the firmware leaving the
    feature-report buffer untouched.  Filling the request with 0xA5 makes the
    two cases distinguishable.  This only sends read requests.
    """
    others = dev.other_writers()
    if others:
        print("[!] Another process has the K61SE open; close it for a clean "
              "test:", file=sys.stderr)
        for pid, cmd in others:
            print(f"      pid {pid:<7} {cmd}", file=sys.stderr)

    tests = [
        (INFO_OP, 6, 1, "info"),
        (0x04, 128, 0, "lighting"),
        (KEYMAP_OP, KEYMAP_SIZE, 0, "keymap"),
        (MACRO_OP, min(args.macro_size, dev.REPORT_LEN - 8), 0, "macro"),
    ]
    handle = dev.open_device()
    try:
        for opcode, size, sub, label in tests:
            try:
                raw = dev.read_raw(opcode, size, sub, dev=handle,
                                   fill=0xA5, attempts=1)
            except Exception as exc:  # noqa: BLE001
                print(f"{label:<8} op 0x{opcode:02X}: failed: {exc}")
                continue
            body = raw[8:8 + size]
            pattern = sum(1 for value in body if value == 0xA5)
            zeros = sum(1 for value in body if value == 0)
            print(
                f"{label:<8} op 0x{opcode:02X} size {size:<4} "
                f"header {raw[:8].hex(' ')}"
            )
            if len(raw) > 1 and raw[1] != (0x80 | opcode):
                print(
                    f"         [!] reply op 0x{raw[1]:02X} does not match "
                    f"requested 0x{0x80 | opcode:02X}"
                )
            print(f"         body {body[:24].hex(' ')}...")
            print(
                f"         A5={pattern}/{len(body)} zeros={zeros}/{len(body)}"
            )
            if len(body) < size:
                print(
                    f"         -> short reply ({len(raw)} bytes): no body; "
                    "the device did not return this block"
                )
            elif pattern == size:
                print("         -> no answer: the device echoed the request")
            elif zeros == size:
                print("         -> real zero-filled reply (device answered)")
            elif pattern:
                print("         -> partial echo / mixed reply")
            else:
                print("         -> device answered with data")
    finally:
        handle.close()
    return 0


def probe(args):
    keys = load_xml_keys()
    others = dev.other_writers()
    if others:
        print("[!] Another process has the K61SE open.  Reads may be consumed; "
              "close it for a clean dump:", file=sys.stderr)
        for pid, cmd in others:
            print(f"      pid {pid:<7} {cmd}", file=sys.stderr)

    save_dir = Path(args.save_dir) if args.save else None
    result = {"read_at": time.strftime("%Y-%m-%d %H:%M:%S"), "layers": {},
              "macros": {}, "info": None}

    handle = dev.open_device()
    try:
        try:
            info = _read(handle, INFO_OP, 6, 1)
            result["info"] = info.hex(" ")
            print(f"Info (0x{INFO_OP:02X}/sub 1): {info.hex(' ')}")
        except Exception as exc:  # noqa: BLE001
            print(f"[!] info read failed: {exc}", file=sys.stderr)

        for layer in args.layers:
            try:
                body = _read(handle, KEYMAP_OP, KEYMAP_SIZE, layer)
            except Exception as exc:  # noqa: BLE001
                print(f"[!] keymap layer {layer} read failed: {exc}", file=sys.stderr)
                continue
            if body and not any(body):
                print(
                    f"[!] layer {layer}: all-zero body. Run --echo-test to "
                    "distinguish a real empty map from a request that was "
                    "ignored/echoed.",
                    file=sys.stderr,
                )
            report = print_keymap(body, keys, layer, full=args.full)
            result["layers"][str(layer)] = {
                "hex": body.hex(),
                "esc_ok": report["esc_ok"],
                "factory_matched": report["matched"],
                "factory_checked": report["checked"],
                "plausible": report["plausible"],
                "looks_like_keymap": report["looks_like_keymap"],
                "cells": [
                    {"index": c["index"], "label": c["label"],
                     "code": c["code"].hex(" "), "summary": c["summary"]}
                    for c in report["cells"]
                ],
            }
            if save_dir:
                path = _save(str(save_dir), f"keymap-layer{layer}.bin", body)
                print(f"  saved {path}")

        try:
            macro = _read_chunks(handle, MACRO_OP, args.macro_size, 0)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] macro read failed: {exc}", file=sys.stderr)
            macro = None
        if macro is not None:
            if macro and not any(macro):
                print(
                    "[!] macro area: all-zero body. Run --echo-test to "
                    "distinguish a real empty area from a request that was "
                    "ignored/echoed.",
                    file=sys.stderr,
                )
            parsed = print_macros(macro)
            result["macros"] = {
                "hex": macro.hex(),
                "count": parsed["count"],
                "used": parsed["used"],
                "truncated": parsed["truncated"],
                "macros": [
                    {
                        "index": m["index"],
                        "addr": m["addr"],
                        "size": m["size"],
                        "name": m.get("name", ""),
                        "name_encoding": m.get("name_encoding", ""),
                        "truncated": m.get("truncated", False),
                        "events": [
                            {"raw": e["raw"].hex(" "), "summary": e["summary"],
                             "type": e["type"], "up": e["up"],
                             "delay_ms": e["delay_ms"], "code": e["code"]}
                            for e in m.get("events", [])
                        ],
                    }
                    for m in parsed["macros"]
                ],
            }
            if save_dir:
                path = _save(str(save_dir), f"macros-{len(macro)}.bin", macro)
                print(f"  saved {path}")
    finally:
        handle.close()

    if save_dir:
        json_path = Path(save_dir) / "probe.json"
        json_path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nSaved summary: {json_path}")
    return 0


# --------------------------------------------------------------------------- #
# offline file decoding
# --------------------------------------------------------------------------- #
def _read_file(path: str) -> bytes:
    data = Path(path).read_bytes()
    # Accept a full report-6 dump (8-byte header + body) as well as a raw body.
    if len(data) >= 8 and data[0] == 0x06:
        size = data[6] | (data[7] << 8)
        if len(data) >= 8 + size:
            return data[8:8 + size]
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only K61SE keymap/macro inspector. "
                    "It never writes keymap or macro data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples", 1)[1] if "Examples" in __doc__ else None,
    )
    parser.add_argument("--probe", action="store_true",
                        help="read keymaps and the macro area from the keyboard")
    parser.add_argument("--echo-test", action="store_true",
                        help="read-only check whether 0x83/0x85 answer or merely "
                             "echo the request (fills the request with 0xA5)")
    parser.add_argument("--layers", default="0,1",
                        help="layers to read with --probe (default 0,1; 'all' = 0..7)")
    parser.add_argument("--macro-size", type=int, default=512,
                        help="macro bytes to request (1..4096, default 512)")
    parser.add_argument("--full", action="store_true",
                        help="show all 126 keymap cells instead of physical keys only")
    parser.add_argument("--save", action="store_true",
                        help="save raw bodies and probe.json under --save-dir")
    parser.add_argument("--save-dir", default="captures/macro-probe",
                        help="directory for --save (default captures/macro-probe)")
    parser.add_argument("--decode-keymap", metavar="FILE",
                        help="decode a saved 0x1F8 keymap body (no device access)")
    parser.add_argument("--decode-macros", metavar="FILE",
                        help="decode a saved macro body (no device access)")
    parser.add_argument("--layer", type=int, default=0,
                        help="layer label for --decode-keymap (default 0)")
    args = parser.parse_args(argv)

    if args.decode_keymap:
        body = _read_file(args.decode_keymap)
        print_keymap(body, load_xml_keys(), args.layer, full=args.full)
        return 0
    if args.decode_macros:
        print_macros(_read_file(args.decode_macros))
        return 0
    if args.echo_test:
        if not 1 <= args.macro_size <= 4096:
            parser.error("--macro-size must be 1..4096")
        return echo_test(args)
    if args.probe:
        if not 1 <= args.macro_size <= 4096:
            parser.error("--macro-size must be 1..4096")
        try:
            args.layers = _parse_layers(args.layers)
        except ValueError as exc:
            parser.error(str(exc))
        return probe(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
