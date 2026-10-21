#!/usr/bin/env python3
"""Explicit K61SE keymap reader/writer.

This is separate from ``k61se_macro.py`` on purpose: the macro tool stays
read-only, while this tool owns the one block that is confirmed on hardware.

The vendor app was captured on 2026-10-20 doing a read-modify-write cycle:

    06 83 00 00 01 00 F8 01   read 504-byte keymap
    06 83 00 00 01 00 F8 01   reply body
    06 03 00 00 01 00 F8 01   write full 504-byte keymap

The body is 126 four-byte actions.  The XML's ``key_index`` is the cell index
directly, so ``offset = 4 * key_index``.  Cell 0 is reserved; cell 1 is the
physical Esc.  (The old app readback had a duplicate Esc in slot 0; the Fn+Tab
reset snapshot leaves it zero.)

Examples (the dry-run examples do not open the keyboard):

    python3 k61se_keymap.py --read
    python3 k61se_keymap.py --list-keys
    python3 k61se_keymap.py --list-actions
    python3 k61se_keymap.py --restore-stock --dry-run
    python3 k61se_keymap.py --set Space=B --dry-run \\
        --base-file captures/keymap-reset.bin
    python3 k61se_keymap.py --set Alt@17="Left Alt" --yes
    python3 k61se_keymap.py --restore-stock --yes

The last two commands write to the keyboard.  They always save a pre-write
readback, send the full 504-byte block, read it back, and compare.  Close the
vendor app first; it re-asserts its own map.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import k61se_dev as dev
import k61se_macro as km

DEFAULT_STOCK = Path(__file__).resolve().parent / "captures" / "keymap-reset.bin"
DEFAULT_SAVE_DIR = Path(__file__).resolve().parent / "captures" / "keymap-write"
KEYMAP_OP = km.KEYMAP_OP
KEYMAP_SIZE = km.KEYMAP_SIZE


# --------------------------------------------------------------------------- #
# names and actions
# --------------------------------------------------------------------------- #
def _norm(text: str) -> str:
    # Keep punctuation: names such as "KP /", "KP *", and "Non-US #" would
    # otherwise all collapse to the same lookup key.
    return re.sub(r"\s+", "", text.lower())


HID_LOOKUP = {_norm(name): usage for usage, name in km.HID_NAMES.items()}
HID_LOOKUP.update(
    {
        "capslock": 0x39,
        "space": 0x2C,
        "spacebar": 0x2C,
        "escape": 0x29,
        "return": 0x28,
        "pageup": 0x4B,
        "pagedown": 0x4E,
        "printscreen": 0x46,
        "scrolllock": 0x47,
        "numlock": 0x53,
        "delete": 0x4C,
        "insert": 0x49,
        "left": 0x50,
        "right": 0x4F,
        "up": 0x52,
        "down": 0x51,
    }
)

MOD_LOOKUP = {_norm(name): bit for bit, name in km.MODIFIER_BITS.items()}
MOD_LOOKUP.update(
    {
        "ctrl": 0x01,
        "shift": 0x02,
        "alt": 0x04,
        "win": 0x08,
        "lctrl": 0x01,
        "lshift": 0x02,
        "lalt": 0x04,
        "lwin": 0x08,
        "rctrl": 0x10,
        "rshift": 0x20,
        "ralt": 0x40,
        "rwin": 0x80,
    }
)

CONSUMER_LOOKUP = {_norm(name): usage for usage, name in km.CONSUMER_NAMES.items()}
CONSUMER_LOOKUP.update(
    {
        "playpause": 0x00CD,
        "next": 0x00B5,
        "previous": 0x00B6,
        "prev": 0x00B6,
        "mute": 0x00E2,
        "volup": 0x00E9,
        "voldown": 0x00EA,
    }
)


def _number(text: str) -> int:
    return int(text.strip(), 0)


def _keyboard_action(usage: int) -> bytes:
    # The map stores modifiers as a bitmap, not as HID usages 0xE0..0xE7.
    if 0xE0 <= usage <= 0xE7:
        return bytes([0, 1 << (usage - 0xE0), 0, 0])
    return bytes([0, 0, 0, usage])


def parse_action(text: str) -> bytes:
    """Parse an action for one keymap cell.

    Supported forms:
      none / disabled / default     00 00 00 00
      B, Space, F1, Left Shift      keyboard usage or modifier
      key:B, hid:0x05               keyboard usage
      mods:0x02                     raw modifier bitmap
      consumer:Play/Pause           consumer action
      macro:0                       macro index 0, play once
      macro:0,1,2                   macro 0, mode 1, count 2
      fn, fn2                       vendor Fn actions
      raw:00 00 00 05               exactly four raw bytes

    The macro payload is written and read back by ``k61se_macro_write.py``;
    ``macro:INDEX`` only adds the keymap reference to that payload.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty action")

    lower = text.lower()
    if lower in ("none", "disabled", "default"):
        return b"\x00\x00\x00\x00"
    if lower == "fn":
        return b"\x0d\x00\x00\x00"
    if lower == "fn2":
        return b"\x0d\x01\x00\x00"

    if lower.startswith("raw:"):
        raw = text.split(":", 1)[1].replace(" ", "").replace(",", "")
        try:
            data = bytes.fromhex(raw)
        except ValueError as exc:
            raise ValueError(f"bad raw action: {text}") from exc
        if len(data) != 4:
            raise ValueError("raw action must be exactly four bytes")
        return data

    if lower.startswith("mods:"):
        value = _number(text.split(":", 1)[1])
        if not 0 <= value <= 0xFF:
            raise ValueError("modifier bitmap must be 0..0xff")
        return bytes([0, value, 0, 0])

    if lower.startswith("key:") or lower.startswith("hid:"):
        name = text.split(":", 1)[1].strip()
        mod = MOD_LOOKUP.get(_norm(name))
        if mod is not None:
            return bytes([0, mod, 0, 0])
        if re.fullmatch(r"0x[0-9a-f]+|\d+", name, re.I):
            usage = _number(name)
        else:
            usage = HID_LOOKUP.get(_norm(name))
        if usage is None:
            raise ValueError(f"unknown HID key {name!r}")
        if not 0 <= usage <= 0xFF:
            raise ValueError("HID usage must be 0..0xff")
        return _keyboard_action(usage)

    if lower.startswith("consumer:"):
        name = text.split(":", 1)[1].strip()
        if re.fullmatch(r"0x[0-9a-f]+|\d+", name, re.I):
            usage = _number(name)
        else:
            usage = CONSUMER_LOOKUP.get(_norm(name))
        if usage is None:
            raise ValueError(f"unknown consumer key {name!r}")
        if not 0 <= usage <= 0xFFFF:
            raise ValueError("consumer usage must be 0..0xffff")
        return bytes([2, 0, (usage >> 8) & 0xFF, usage & 0xFF])

    if lower.startswith("macro:"):
        parts = [part.strip() for part in text.split(":", 1)[1].split(",")]
        if not parts or not parts[0]:
            raise ValueError("macro action needs an index, e.g. macro:0")
        index = _number(parts[0])
        mode = _number(parts[1]) if len(parts) > 1 and parts[1] else 1
        count = _number(parts[2]) if len(parts) > 2 and parts[2] else 1
        for value, name in ((index, "index"), (mode, "mode"), (count, "count")):
            if not 0 <= value <= 0xFF:
                raise ValueError(f"macro {name} must be 0..255")
        return bytes([3, mode, count, index])

    mod = MOD_LOOKUP.get(_norm(text))
    if mod is not None:
        return bytes([0, mod, 0, 0])

    usage = HID_LOOKUP.get(_norm(text))
    if usage is not None:
        return _keyboard_action(usage)

    if re.fullmatch(r"0x[0-9a-f]+|\d+", text, re.I):
        usage = _number(text)
        if 0 <= usage <= 0xFF:
            return _keyboard_action(usage)

    raise ValueError(
        f"unknown action {text!r}; try a key name, 'none', or 'raw:00 00 00 05'"
    )


def resolve_key(token: str, keys):
    """Resolve a key name or ``Name@index`` to (key_index, display_name)."""
    token = token.strip()
    if not token:
        raise ValueError("empty key name")

    if "@" in token:
        name, number = token.rsplit("@", 1)
        try:
            index = _number(number)
        except ValueError as exc:
            raise ValueError(f"bad key index in {token!r}") from exc
        if index not in keys:
            raise ValueError(f"no physical key at index {index}")
        return index, keys[index][0]["name"]

    if re.fullmatch(r"\d+", token):
        index = int(token, 10)
        if index not in keys:
            raise ValueError(f"no physical key at index {index}")
        return index, keys[index][0]["name"]

    wanted = _norm(token)
    matches = []
    for index, entries in keys.items():
        for entry in entries:
            if wanted in (_norm(entry["name"]), _norm(entry["desc"])):
                matches.append(index)
                break
    matches = sorted(set(matches))
    if len(matches) == 1:
        return matches[0], keys[matches[0]][0]["name"]
    if not matches:
        raise ValueError(f"unknown key {token!r}")
    choices = ", ".join(
        f"{keys[index][0]['name']}@{index} ({keys[index][0]['row_col']})"
        for index in matches
    )
    raise ValueError(f"ambiguous key {token!r}; use one of: {choices}")


# --------------------------------------------------------------------------- #
# files and reports
# --------------------------------------------------------------------------- #
def load_body_file(path: str) -> bytes:
    """Load a raw 504-byte body or a report-6 frame (write or read reply)."""
    data = Path(path).read_bytes()
    if len(data) == KEYMAP_SIZE:
        return data
    if len(data) >= 8 and data[0] == 0x06:
        size = data[6] | (data[7] << 8)
        if size == KEYMAP_SIZE and len(data) >= 8 + size:
            return data[8:8 + size]
    raise ValueError(
        f"{path} is not a {KEYMAP_SIZE}-byte keymap body or a report-6 keymap frame"
    )


def save_body(directory: Path, name: str, body: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(body)
    return path


def unique_stamp(directory: Path) -> str:
    """A timestamp that will not overwrite an earlier pre/write/post set."""
    base = time.strftime("%Y%m%d-%H%M%S")
    stamp = base
    serial = 1
    while (directory / f"pre-{stamp}.bin").exists():
        stamp = f"{base}-{serial}"
        serial += 1
    return stamp


def keymap_diff(before: bytes, after: bytes, keys):
    changes = []
    for offset in range(0, min(len(before), len(after)), 4):
        old = bytes(before[offset:offset + 4])
        new = bytes(after[offset:offset + 4])
        if old == new:
            continue
        index = offset // 4
        entries = keys.get(index, [])
        label = "slot 0 (reserved)" if index == 0 else " / ".join(
            entry["name"] for entry in entries
        )
        changes.append(
            {
                "index": index,
                "label": label,
                "before": old.hex(" "),
                "after": new.hex(" "),
                "before_summary": km.describe_code(old),
                "after_summary": km.describe_code(new),
            }
        )
    return changes


def print_changes(changes):
    if not changes:
        print("  (no keymap cells changed)")
        return
    for change in changes:
        print(
            f"  key {change['index']:>3} {change['label']:<18} "
            f"{change['before']} -> {change['after']}  "
            f"{change['after_summary']}"
        )


def read_current() -> bytes:
    body = bytes(dev.read_body(KEYMAP_OP, KEYMAP_SIZE, 0))
    if len(body) != KEYMAP_SIZE:
        raise OSError(f"keymap read returned {len(body)} bytes, wanted {KEYMAP_SIZE}")
    return body


def warn_other_writers():
    others = dev.other_writers()
    if not others:
        return
    print("[!] Another process has the K61SE open. Close the vendor app for a "
          "clean read/write:", file=sys.stderr)
    for pid, cmd in others:
        print(f"      pid {pid:<7} {cmd}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def list_keys(keys):
    print(f"{'cell':>4}  {'name':<12} {'row_col':<8} XML code")
    for index in sorted(keys):
        for entry in keys[index]:
            print(
                f"{index:>4}  {entry['name']:<12} {entry['row_col']:<8} "
                f"0x{entry['code']:02X}"
            )


def list_actions():
    print("Keyboard / HID usages:")
    for usage in sorted(km.HID_NAMES):
        print(f"  0x{usage:02X}  {km.HID_NAMES[usage]}")
    print("\nModifier actions (produce [00 bit 00 00]):")
    for bit, name in sorted(km.MODIFIER_BITS.items()):
        print(f"  0x{bit:02X}  {name}")
    print("\nConsumer actions (use consumer:NAME):")
    for usage, name in sorted(km.CONSUMER_NAMES.items()):
        print(f"  0x{usage:03X}  {name}")
    print("\nSpecial actions:")
    print("  none / disabled / default")
    print("  key:NAME / key:0x05")
    print("  mods:0x02")
    print("  consumer:NAME / consumer:0xCD")
    print("  macro:INDEX[,MODE[,COUNT]]  (index 0 = first macro)")
    print("  fn, fn2")
    print("  raw:00 00 00 05")


def read_command(args, keys):
    if args.base_file:
        body = load_body_file(args.base_file)
        source = args.base_file
    else:
        warn_other_writers()
        body = read_current()
        source = "device"
    print(f"Keymap source: {source}")
    km.print_keymap(body, keys, 0, full=args.full)
    return 0


def choose_base(args, keys, pre):
    if args.base_file:
        return load_body_file(args.base_file), f"file {args.base_file}"

    if args.base == "stock":
        body = load_body_file(args.stock_file)
        return body, f"stock {args.stock_file}"

    if pre is None:
        pre = read_current()

    if args.base == "device":
        return pre, "device readback"

    # auto: use the device map when it looks populated, otherwise the stock
    # baseline.  A factory-reset keyboard can return an all-zero keymap.
    if not any(pre):
        body = load_body_file(args.stock_file)
        return body, f"stock {args.stock_file} (device readback was all zero)"
    report = km.keymap_report(pre, keys)
    if report["looks_like_keymap"]:
        return pre, "device readback"
    body = load_body_file(args.stock_file)
    return body, f"stock {args.stock_file} (device readback was not a keymap)"


def prepare_write(args, keys, parser):
    mode = "set" if args.set else "restore" if args.restore_stock else "write"
    pre = None
    baseline = None
    baseline_label = f"stock {args.stock_file}"

    # A dry-run should stay offline when a file already supplies the base.
    needs_pre = (
        (mode == "set" and not args.base_file and args.base in ("auto", "device"))
        or (mode in ("set", "restore", "write") and not args.dry_run)
    )
    if needs_pre:
        pre = read_current()

    if mode == "restore":
        body = load_body_file(args.stock_file)
        source = f"stock {args.stock_file}"
        report = km.keymap_report(body, keys)
        print(f"Base: {source}")
        print(
            f"Stock baseline: Esc(1)={'OK' if report['esc_ok'] else 'bad'}, "
            f"XML matches {report['matched']}/{report['checked']}"
        )
        if not report["esc_ok"] or report["matched"] != report["checked"]:
            parser.error(
                f"{args.stock_file} does not match the XML factory map "
                f"({report['matched']}/{report['checked']}); refusing to write it"
            )
    elif mode == "write":
        body = load_body_file(args.write)
        source = f"file {args.write}"
        if not any(body):
            parser.error(
                "body is all zero; refusing to write an empty keymap (this is "
                "usually an empty read request, not a saved map)"
            )
        report = km.keymap_report(body, keys)
        print(
            f"Body check: Esc(1)={'OK' if report['esc_ok'] else 'bad'}, "
            f"XML matches {report['matched']}/{report['checked']}, "
            f"looks like keymap={report['looks_like_keymap']}"
        )
        if not report["looks_like_keymap"] and not args.force:
            parser.error(
                "body does not look like a keymap; pass --force to write it anyway"
            )
    else:
        body, source = choose_base(args, keys, pre)
        baseline = body
        baseline_label = f"base ({source})"
        report = km.keymap_report(body, keys)
        if not report["esc_ok"] or not report["looks_like_keymap"]:
            print(
                "[!] base keymap does not look like the factory layout; pass "
                "--force only if that is deliberate",
                file=sys.stderr,
            )
            if not args.force:
                parser.error("base keymap does not look usable")

        body = bytearray(body)
        changes = []
        for spec in args.set:
            if "=" not in spec:
                parser.error(f"--set needs KEY=ACTION, got {spec!r}")
            key_text, action_text = spec.split("=", 1)
            try:
                index, label = resolve_key(key_text, keys)
                action = parse_action(action_text)
            except ValueError as exc:
                parser.error(str(exc))
            old = bytes(body[index * 4:index * 4 + 4])
            body[index * 4:index * 4 + 4] = action
            changes.append(
                {
                    "index": index,
                    "label": label,
                    "before": old.hex(" "),
                    "after": action.hex(" "),
                    "before_summary": km.describe_code(old),
                    "after_summary": km.describe_code(action),
                    "requested": spec,
                }
            )
        body = bytes(body)
        print(f"Base: {source}")
        print("Requested changes:")
        for change in changes:
            print(
                f"  key {change['index']:>3} {change['label']:<18} "
                f"{change['before']} -> {change['after']}  "
                f"{change['after_summary']}"
            )

    print("\nFinal keymap differences:")
    if pre is None:
        if baseline is None:
            baseline = load_body_file(args.stock_file)
        print(f"  (no device readback; comparing with {baseline_label})")
        print_changes(keymap_diff(baseline, body, keys))
    else:
        print_changes(keymap_diff(pre, body, keys))

    if args.dry_run:
        print("\nDry run: no device write, no files saved.")
        return 0, mode, source, pre, body, None

    if not args.yes:
        print("\nRefusing to write without --yes.")
        return 2, mode, source, pre, body, None

    save_dir = Path(args.save_dir)
    stamp = unique_stamp(save_dir)
    pre_path = save_body(save_dir, f"pre-{stamp}.bin", pre)
    write_path = save_body(save_dir, f"write-{stamp}.bin", body)
    print(f"\nSaved pre-write readback: {pre_path}")
    print(f"Saved body to write:      {write_path}")

    print(f"Writing 0x{KEYMAP_OP:02X}, {len(body)} bytes, sub=0 ...")
    written = dev.write_body(KEYMAP_OP, body, sub=0)
    print(f"Write accepted ({written} bytes transferred).")
    time.sleep(0.10)

    post = read_current()
    for _ in range(2):
        if post == body:
            break
        time.sleep(0.10)
        post = read_current()
    post_path = save_body(save_dir, f"post-{stamp}.bin", post)
    print(f"Saved post-write readback: {post_path}")
    diff = keymap_diff(body, post, keys)
    verified = post == body
    if verified:
        print("Readback matches the body written.")
    else:
        print("[!] Readback differs from the body written:")
        print_changes(diff)

    result = {
        "timestamp": stamp,
        "mode": mode,
        "source": source,
        "pre_file": str(pre_path),
        "write_file": str(write_path),
        "post_file": str(post_path),
        "requested": args.set,
        "pre_to_write_diff": keymap_diff(pre, body, keys),
        "verified": verified,
        "post_diff": diff,
    }
    result_path = save_dir / f"result-{stamp}.json"
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Saved result: {result_path}")
    return 0 if verified else 1, mode, source, pre, body, post


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read or explicitly write the K61SE 504-byte keymap. "
                    "Writes require --yes and always read back.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples", 1)[1] if "Examples" in __doc__ else None,
    )
    parser.add_argument("--read", action="store_true",
                        help="read and decode the current keymap (read-only)")
    parser.add_argument("--restore-stock", action="store_true",
                        help="write the captured Fn+Tab reset keymap")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=ACTION",
                        help="remap one key; repeatable, e.g. --set Space=B")
    parser.add_argument("--write", metavar="FILE",
                        help="write a saved 504-byte body or report-6 keymap frame")
    parser.add_argument("--base", choices=("auto", "stock", "device"),
                        default="auto",
                        help="base for --set (default: auto)")
    parser.add_argument("--base-file", metavar="FILE",
                        help="use a saved body as the base for --set or --read")
    parser.add_argument("--stock-file", default=str(DEFAULT_STOCK),
                        help=f"stock baseline for --restore-stock (default: {DEFAULT_STOCK})")
    parser.add_argument("--save-dir", default=str(DEFAULT_SAVE_DIR),
                        help="directory for pre/write/post evidence")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the intended changes without writing")
    parser.add_argument("--yes", action="store_true",
                        help="confirm a device write")
    parser.add_argument("--force", action="store_true",
                        help="allow an unusual body or an open vendor app")
    parser.add_argument("--full", action="store_true",
                        help="show all 126 cells with --read")
    parser.add_argument("--list-keys", action="store_true",
                        help="list XML key names/indexes (no device)")
    parser.add_argument("--list-actions", action="store_true",
                        help="list usable action names (no device)")
    args = parser.parse_args(argv)

    keys = km.load_xml_keys()

    if args.list_keys:
        list_keys(keys)
        return 0
    if args.list_actions:
        list_actions()
        return 0

    modes = [
        args.read,
        args.restore_stock,
        bool(args.set),
        bool(args.write),
    ]
    if sum(bool(mode) for mode in modes) != 1:
        parser.error("choose exactly one of --read, --restore-stock, --set, --write")
    if args.base_file and (args.restore_stock or args.write):
        parser.error("--base-file is only for --read and --set")

    if args.read:
        return read_command(args, keys)

    others = dev.other_writers()
    if others:
        print(
            "[!] Another process has the K61SE open. Close the vendor app for a "
            "clean write:",
            file=sys.stderr,
        )
        for pid, cmd in others:
            print(f"      pid {pid:<7} {cmd}", file=sys.stderr)
        if not args.dry_run and not args.force:
            parser.error("close the vendor app before writing (or pass --force)")

    return prepare_write(args, keys, parser)[0]


if __name__ == "__main__":
    raise SystemExit(main())
