#!/usr/bin/env python3
"""One-shot K61SE macro writer for hardware validation.

This is deliberately separate from the read-only ``k61se_macro.py``.  It builds
one F75-style macro, writes the whole macro block with opcode ``0x05``, reads it
back with ``0x85``, and compares the two.  It does **not** bind the macro to a
key and it does not touch the keymap.

The layout is the AULA F75 one recovered from ``macros.rs`` and cross-checked
against KBAccess's ``SetMacro``:

    N x [addr u16, size u16]        table at the start
    name_len u8, UTF-16LE name, events...
    event = [(type<<4)|(up<<7)|delay[19:16], delay[15:8], delay[7:0], code]

The first K61SE run (2026-10-20, ``--name test1 --text abc``) wrote a 39-byte
body and the ``0x85`` readback prefix matched exactly.  Playback and key binding
are still under test.  The tool always saves the old macro area first, writes
only one macro, and reads the area back.  If the readback does not match, hold
Fn+Tab for 5 seconds to return the keyboard to its reset state.

Examples (the dry run does not open the keyboard):

    # Inspect the bytes without touching the keyboard
    python3 k61se_macro_write.py --name test1 --text abc --dry-run

    # Explicit write + readback; no key binding
    python3 k61se_macro_write.py --name test1 --text abc --yes

    # If the name length seems wrong, try the KBAccess character-count form
    python3 k61se_macro_write.py --name test1 --text abc \
        --name-length chars --yes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import k61se_dev as dev
import k61se_macro as km

MACRO_OP = 0x05
MACRO_SIZE = 0x1000
DEFAULT_SAVE_DIR = Path(__file__).resolve().parent / "captures" / "macro-write"
DEFAULT_DELAY = 20
DEFAULT_TAP_DELAY = 4
MAX_NAME_CHARS = 30

# US keyboard usage page 0x07, same mapping as the F75 macros.rs builder.
PLAIN = {
    "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "5": 0x22,
    "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27,
    "\n": 0x28, "\t": 0x2B, " ": 0x2C,
    "-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30, "\\": 0x31,
    ";": 0x33, "'": 0x34, "`": 0x35, ",": 0x36, ".": 0x37,
    "/": 0x38,
}
SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
    "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
}
SHIFTED2 = {
    "_": 0x2D, "+": 0x2E, "{": 0x2F, "}": 0x30, "|": 0x31,
    ":": 0x33, '"': 0x34, "~": 0x35, "<": 0x36, ">": 0x37, "?": 0x38,
}


def char_usage(ch: str):
    """Return (usage, needs_shift) or None for an unsupported character."""
    if "a" <= ch <= "z":
        return 0x04 + ord(ch) - ord("a"), False
    if "A" <= ch <= "Z":
        return 0x04 + ord(ch) - ord("A"), True
    if ch in PLAIN:
        return PLAIN[ch], False
    if ch in SHIFTED:
        return PLAIN[SHIFTED[ch]], True
    if ch in SHIFTED2:
        return SHIFTED2[ch], True
    return None


def text_events(text: str, delay: int, tap_delay: int):
    """Build F75 key events: optional Shift down/up around each character."""
    events = []
    unsupported = sorted({ch for ch in text if char_usage(ch) is None})
    if unsupported:
        shown = " ".join(repr(ch) for ch in unsupported)
        raise ValueError(f"unsupported character(s): {shown}")
    for ch in text:
        usage, shift = char_usage(ch)
        if shift:
            events.append((0xE1, True, tap_delay))
        events.append((usage, True, delay))
        events.append((usage, False, tap_delay))
        if shift:
            events.append((0xE1, False, delay))
    return events


def encode_event(usage: int, down: bool, delay_ms: int) -> bytes:
    delay = max(0, min(int(delay_ms), 0xFFFFF))
    kind = 1 if 0xE0 <= usage <= 0xE7 else 0
    head = (kind << 4) | ((delay >> 16) & 0x0F)
    if not down:
        head |= 0x80
    return bytes([head, (delay >> 8) & 0xFF, delay & 0xFF, usage & 0xFF])


def macro_blob(name: str, text: str, delay: int, tap_delay: int,
               name_length: str):
    if len(name) > MAX_NAME_CHARS:
        print(
            f"[!] macro name is longer than {MAX_NAME_CHARS} chars; truncating",
            file=sys.stderr,
        )
        name = name[:MAX_NAME_CHARS]
    name_bytes = name.encode("utf-16le")
    if name_length == "bytes":
        length_byte = len(name_bytes)
    else:
        length_byte = len(name)
    if length_byte > 0xFF:
        raise ValueError("macro name is too long for the one-byte length field")
    events = text_events(text, delay, tap_delay)
    blob = bytes([length_byte]) + name_bytes
    for usage, down, event_delay in events:
        blob += encode_event(usage, down, event_delay)
    return blob


def build_buffer(name: str, text: str, delay: int, tap_delay: int,
                 name_length: str):
    blob = macro_blob(name, text, delay, tap_delay, name_length)
    if 4 + len(blob) > MACRO_SIZE:
        raise ValueError(
            f"macro uses {4 + len(blob)} bytes, over the {MACRO_SIZE}-byte area"
        )
    # One macro; the first blob starts immediately after the four-byte table.
    return (4).to_bytes(2, "little") + len(blob).to_bytes(2, "little") + blob


def unique_stamp(directory: Path) -> str:
    base = time.strftime("%Y%m%d-%H%M%S")
    stamp = base
    serial = 1
    while (directory / f"pre-{stamp}.bin").exists():
        stamp = f"{base}-{serial}"
        serial += 1
    return stamp


def save_body(directory: Path, name: str, body: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(body)
    return path


def dump_bytes(body: bytes, limit=64):
    shown = body[:limit].hex(" ")
    return shown + (" ..." if len(body) > limit else "")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Write one F75-style macro to the K61SE with a pre-dump "
                    "and readback. No key binding is changed.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples", 1)[1] if "Examples" in __doc__ else None,
    )
    parser.add_argument("--name", default="test1",
                        help="macro name (default: test1)")
    parser.add_argument("--text", required=True,
                        help="characters to type, e.g. abc or 'Hello world'")
    parser.add_argument("--delay", type=int, default=DEFAULT_DELAY,
                        help=f"delay after each key press in ms "
                             f"(default: {DEFAULT_DELAY})")
    parser.add_argument("--tap-delay", type=int, default=DEFAULT_TAP_DELAY,
                        help=f"delay for key-up/shift events in ms "
                             f"(default: {DEFAULT_TAP_DELAY})")
    parser.add_argument("--name-length", choices=("bytes", "chars"),
                        default="bytes",
                        help="name length field: F75 byte count (default) or "
                             "KBAccess character count")
    parser.add_argument("--save-dir", default=str(DEFAULT_SAVE_DIR),
                        help="directory for pre/write/post evidence")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the body without opening the keyboard")
    parser.add_argument("--yes", action="store_true",
                        help="confirm writing the macro block")
    parser.add_argument("--force", action="store_true",
                        help="allow an open vendor app")
    args = parser.parse_args(argv)

    if args.delay < 0 or args.delay > 0xFFFFF:
        parser.error("--delay must be 0..0xfffff")
    if args.tap_delay < 0 or args.tap_delay > 0xFFFFF:
        parser.error("--tap-delay must be 0..0xfffff")
    if not args.name:
        parser.error("--name must not be empty")
    if not args.text:
        parser.error("--text must not be empty")

    try:
        body = build_buffer(
            args.name, args.text, args.delay, args.tap_delay, args.name_length
        )
    except ValueError as exc:
        parser.error(str(exc))

    print(f"Macro name: {args.name!r}")
    print(f"Text:       {args.text!r}")
    print(f"Body:       {len(body)} bytes (area limit {MACRO_SIZE})")
    print(f"Name field: {args.name_length} ({body[4]} bytes/units)")
    print(f"Hex:        {dump_bytes(body)}")
    km.print_macros(body + bytes(MACRO_SIZE - len(body)))

    if args.dry_run:
        print("\nDry run: no device access and no write.")
        return 0

    if not args.yes:
        print("\nRefusing to write without --yes.")
        return 2

    others = dev.other_writers()
    if others and not args.force:
        print("[!] Another process has the K61SE open:", file=sys.stderr)
        for pid, cmd in others:
            print(f"      pid {pid:<7} {cmd}", file=sys.stderr)
        print("    Close the vendor app, or pass --force.", file=sys.stderr)
        return 2

    save_dir = Path(args.save_dir)
    stamp = unique_stamp(save_dir)
    print("\n[*] Reading the current macro area ...")
    try:
        pre = bytes(dev.read_body_chunks(MACRO_OP, MACRO_SIZE, 0))
    except OSError as exc:
        print(f"[!] Macro read failed: {exc}", file=sys.stderr)
        return 1
    pre_path = save_body(save_dir, f"pre-{stamp}.bin", pre)
    write_path = save_body(save_dir, f"write-{stamp}.bin", body)
    print(f"    saved pre:   {pre_path}")
    print(f"    saved write: {write_path}")
    if any(pre):
        print("[!] The macro area was not empty. The app may leave old blobs "
              "beyond the new table.", file=sys.stderr)

    print(f"[*] Writing op 0x{MACRO_OP:02X}, {len(body)} bytes, sub=0 ...")
    try:
        written = dev.write_body(MACRO_OP, body, sub=0)
    except OSError as exc:
        print(f"[!] Macro write failed: {exc}", file=sys.stderr)
        print("    Recovery: hold Fn+Tab for 5 seconds.", file=sys.stderr)
        return 1
    print(f"    write accepted ({written} bytes transferred)")

    time.sleep(0.12)
    post = None
    last_error = None
    for _ in range(3):
        try:
            post = bytes(dev.read_body_chunks(MACRO_OP, MACRO_SIZE, 0))
        except OSError as exc:
            post = None
            last_error = exc
        if post is not None and post[:len(body)] == body:
            break
        time.sleep(0.12)

    if post is None:
        print(f"[!] Macro readback failed: {last_error}", file=sys.stderr)
        print("    Recovery: hold Fn+Tab for 5 seconds.", file=sys.stderr)
        return 1
    post_path = save_body(save_dir, f"post-{stamp}.bin", post)
    print(f"    saved post:  {post_path}")

    prefix_ok = post[:len(body)] == body
    trailing_nonzero = any(post[len(body):])
    verified = prefix_ok
    result = {
        "timestamp": stamp,
        "mode": "one-shot-f75-macro",
        "name": args.name,
        "text": args.text,
        "delay_ms": args.delay,
        "tap_delay_ms": args.tap_delay,
        "name_length": args.name_length,
        "body_size": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "pre_file": str(pre_path),
        "write_file": str(write_path),
        "post_file": str(post_path),
        "prefix_match": prefix_ok,
        "trailing_nonzero": trailing_nonzero,
        "verified": verified,
        "keymap_binding_changed": False,
    }
    result_path = save_dir / f"result-{stamp}.json"
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"    saved result: {result_path}")

    if prefix_ok:
        print("\n[+] Readback prefix matches the macro body.")
        if trailing_nonzero:
            print("[!] Bytes after the body are non-zero; this can be old "
                  "macro data and is not necessarily a failure.", file=sys.stderr)
        print("[*] Macro is written but NOT bound to a key.")
        print("    Verify the decoded readback above before using it.")
    else:
        print("\n[!] Readback differs from the body written.", file=sys.stderr)
        for offset, (old, new) in enumerate(zip(body, post)):
            if old != new:
                print(
                    f"      first difference at 0x{offset:03X}: "
                    f"{old:02X} -> {new:02X}",
                    file=sys.stderr,
                )
                break
        print("    Recovery: hold Fn+Tab for 5 seconds.", file=sys.stderr)
        print("    If the name field looks wrong, retry with "
              "--name-length chars.", file=sys.stderr)

    if post:
        km.print_macros(post)
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
