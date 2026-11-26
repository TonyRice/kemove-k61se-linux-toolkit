#!/usr/bin/env python3
"""Read-only HID inventory for KEMOVE/AULA keyboards.

This only enumerates HID interfaces; it does not open the device or send any
reports.  Use it to identify the exact model, product string, interface, and
usage page before running a writer.

    python3 -m kemove.probe
    python3 -m kemove.probe --all
    python3 -m kemove.probe --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .devices import AULA_VID, G5KB, KEMOVE_VID, identify, profile_for_key

KNOWN_VIDS = {KEMOVE_VID, AULA_VID}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _rows(all_devices: bool = False) -> list[dict[str, Any]]:
    try:
        import hid
    except ImportError as exc:  # pragma: no cover - depends on the host
        raise SystemExit("python hidapi is not installed (python3 -m pip install hid)") from exc

    rows: list[dict[str, Any]] = []
    for info in hid.enumerate():
        vid = int(info.get("vendor_id") or 0)
        pid = int(info.get("product_id") or 0)
        if not all_devices and vid not in KNOWN_VIDS:
            continue
        profile = identify(info)
        rows.append(
            {
                "vid_pid": f"{vid:04X}:{pid:04X}",
                "vendor_id": vid,
                "product_id": pid,
                "product": _text(info.get("product_string")),
                "manufacturer": _text(info.get("manufacturer_string")),
                "serial": _text(info.get("serial_number")),
                "interface": info.get("interface_number"),
                "usage_page": info.get("usage_page"),
                "usage": info.get("usage"),
                "path": _text(info.get("path")),
                "profile": profile.key if profile else None,
                "profile_name": profile.name if profile else None,
                "transport": profile.transport if profile else None,
                "verified": profile.verified if profile else None,
                "notes": profile.notes if profile else "",
            }
        )
    return rows


def _print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No KEMOVE/AULA HID interfaces found.")
        print("Check the cable/receiver and run again with --all to list every HID device.")
        return

    headers = ("VID:PID", "MI", "usage", "product", "profile", "path")
    values = []
    for row in rows:
        usage = ""
        if row["usage_page"] is not None:
            usage = f"{row['usage_page']:#06x}"
            if row["usage"] is not None:
                usage += f":{row['usage']:#06x}"
        profile = row["profile"] or "unrecognised"
        if row["profile"]:
            profile += "" if row["verified"] else " (provisional)"
        values.append(
            (
                row["vid_pid"],
                "-" if row["interface"] is None else str(row["interface"]),
                usage or "-",
                row["product"] or "-",
                profile,
                row["path"] or "-",
            )
        )

    widths = [len(header) for header in headers]
    for value in values:
        for i, cell in enumerate(value):
            widths[i] = min(48, max(widths[i], len(cell)))
    line = "  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))
    print(line)
    print("  ".join("-" * width for width in widths))
    for row in values:
        print("  ".join(cell[: widths[i]].ljust(widths[i]) for i, cell in enumerate(row)))


def _read_keymap(row: dict[str, Any], size: int) -> bytes:
    """Send one read-only G5KB keymap request and return the body.

    This is deliberately a read: opcode 0x83 has the read bit set.  It does not
    write a block or change any device state.
    """
    if row.get("transport") != G5KB:
        raise SystemExit(
            f"{row.get('profile') or 'device'} uses {row.get('transport') or 'an unknown transport'}; "
            "the G5KB keymap reader only handles 520-byte report-6 devices"
        )

    import hid

    dev = hid.device()
    dev.open_path(row["path"].encode() if isinstance(row["path"], str) else row["path"])
    try:
        req = bytes([0x06, 0x83, 0x00, 0x00, 0x01, 0x00, size & 0xFF, (size >> 8) & 0xFF])
        req += bytes(520 - len(req))
        written = dev.send_feature_report(req)
        if written < 0:
            raise OSError(f"read request failed: {dev.error()}")
        time.sleep(0.03)
        raw = bytes(dev.get_feature_report(0x06, 520))
    finally:
        dev.close()

    if len(raw) >= 8 and raw[0] == 0x06 and (raw[1] & 0x7F) == 0x03:
        body = raw[8:]
    elif raw and (raw[0] & 0x7F) == 0x03:
        body = raw[7:]
    else:
        raise OSError(f"unexpected keymap reply ({len(raw)} bytes): {raw[:16].hex(' ')}")
    if len(body) < size:
        raise OSError(f"short keymap reply: wanted {size} bytes, got {len(body)}")
    return body[:size]


def _print_keymap(row: dict[str, Any], body: bytes, path: Path | None) -> None:
    print(f"device: {row['product']} ({row['vid_pid']}, {row['path']})")
    print(f"profile: {row['profile']} ({'verified' if row['verified'] else 'provisional'})")
    print(f"keymap body: {len(body)} bytes ({len(body) // 4} four-byte cells)")
    print(f"first 64 bytes: {body[:64].hex(' ')}")

    cells = [body[i : i + 4] for i in range(0, len(body), 4)]
    nonzero = [(i, cell) for i, cell in enumerate(cells) if any(cell)]
    print(f"non-empty cells: {len(nonzero)}")
    for i, cell in nonzero[:24]:
        print(f"  cell {i:3d} (offset 0x{i * 4:03x}): {cell.hex(' ')}")
    if len(nonzero) > 24:
        print(f"  ... {len(nonzero) - 24} more")
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        print(f"saved raw body: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="list every HID interface, not just KEMOVE/AULA")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--read-keymap",
        action="store_true",
        help="send one read-only G5KB keymap request to the first recognised device",
    )
    parser.add_argument(
        "--keymap-size",
        type=int,
        default=0,
        help="keymap body size for --read-keymap (default: profile value or 504)",
    )
    parser.add_argument(
        "--save",
        type=Path,
        help="save the raw keymap body for offline comparison",
    )
    args = parser.parse_args(argv)

    rows = _rows(all_devices=args.all)
    if args.read_keymap:
        candidates = [row for row in rows if row.get("profile") and row.get("transport") == G5KB]
        if not candidates:
            print("No recognised G5KB keyboard found for --read-keymap.", file=sys.stderr)
            return 1
        row = candidates[0]
        profile = profile_for_key(str(row["profile"]))
        size = args.keymap_size or (profile.keymap_len if profile and profile.keymap_len else 504)
        body = _read_keymap(row, size)
        _print_keymap(row, body, args.save)
        return 0

    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
