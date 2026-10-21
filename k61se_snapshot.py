#!/usr/bin/env python3
"""K61SE state snapshots, offline diffs, and explicit golden-state restore.

A snapshot is a directory containing ``snapshot.json`` plus one raw body per
block.  Capture and diff are read-only.  ``--restore`` is an explicit writer:
it saves a pre-restore snapshot, writes the supported blocks, and verifies each
block by reading it back.

The capture covers the blocks needed for a golden-reset comparison:

    info       0x82 sub 1       6 B       read-only
    lighting   0x84            128 B       mode + per-effect settings
    colour     0x8A            420 B       21-byte record per effect
    keymap     0x83            504 B       one body per layer (0..7)
    macros     0x85          <=4096 B      one table/blob block

The keymap body is the same one written by ``k61se_keymap.py``.  Snapshot
restore records macros but does not write them; a validated one-shot macro
payload writer lives in ``k61se_macro_write.py``.  Keymap layer 0 is the
hardware-confirmed write; other layers need ``--force`` and are written only
when explicitly selected.

Capture and restore must be run by the device owner.  The vendor app has to be
closed: it consumes report 6 and its state changes between blocks.

Examples:

    # Before a reset
    python3 k61se_snapshot.py --capture captures/reset-before

    # Hold Fn + Tab for 5 seconds, then
    python3 k61se_snapshot.py --capture captures/reset-after

    # Offline: show exactly what the reset changed
    python3 k61se_snapshot.py --diff captures/reset-before captures/reset-after

    # Explicitly write the golden blocks back (lighting, colour, keymap layer 0)
    python3 k61se_snapshot.py --restore captures/golden-reset --yes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import k61se_dev as dev
import k61se_layout as layout
import k61se_macro as km

KEYMAP_OP = 0x03
KEYMAP_SIZE = 504
MACRO_OP = 0x05
MACRO_MAX = 0x1000
INFO_OP = 0x02
INFO_SIZE = 6
LIGHTING_OP = 0x04
COLOUR_OP = 0x0A
SNAPSHOT_SCHEMA = 1
SNAPSHOT_KIND = "k61se-snapshot"


def parse_layers(text: str):
    if text.strip().lower() == "all":
        return list(range(8))
    layers = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            first, last = part.split("-", 1)
            start, end = int(first, 0), int(last, 0)
            if start > end:
                start, end = end, start
            layers.update(range(start, end + 1))
        else:
            layers.add(int(part, 0))
    bad = [layer for layer in layers if not 0 <= layer <= 7]
    if bad:
        raise ValueError(f"layers must be 0..7, got {bad}")
    if not layers:
        raise ValueError("no layers selected")
    return sorted(layers)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_block(op, size, sub=0, chunks=False):
    """Read one body, returning (body, None) or (None, error text)."""
    try:
        if chunks:
            body = bytes(dev.read_body_chunks(op, size, sub))
        else:
            body = bytes(dev.read_body(op, size, sub))
        if len(body) != size:
            return None, f"wanted {size} bytes, got {len(body)}"
        return body, None
    except Exception as exc:  # noqa: BLE001 - report one block without losing the rest
        return None, f"{type(exc).__name__}: {exc}"


def block_record(
    read_op: int,
    write_op,
    sub: int,
    size: int,
    filename: str,
    body: bytes,
    restore: str,
    extra=None,
):
    record = {
        "read_op": f"0x{read_op:02X}",
        "write_op": f"0x{write_op:02X}" if write_op is not None else None,
        "sub": sub,
        "size": size,
        "file": filename,
        "sha256": sha256(body),
        "restore": restore,
    }
    if extra:
        record.update(extra)
    return record


def capture(args, parser):
    out_dir = Path(args.capture)
    if out_dir.exists():
        if out_dir.is_file():
            parser.error(f"{out_dir} is a file, not a snapshot directory")
        if any(out_dir.iterdir()) and not args.force:
            parser.error(f"{out_dir} is not empty; pass --force to overwrite")

    others = dev.other_writers()
    if others and not args.force:
        print(
            "[!] Another process has the K61SE open. Close the vendor app so "
            "the snapshot is consistent:",
            file=sys.stderr,
        )
        for pid, cmd in others:
            print(f"      pid {pid:<7} {cmd}", file=sys.stderr)
        print("    Re-run with --force only if you accept a moving snapshot.",
              file=sys.stderr)
        return 2
    if others:
        print("[!] Capturing with another writer open; snapshot may be moving.",
              file=sys.stderr)

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "snapshot.json"
    if args.force and manifest_path.exists():
        # Do not leave an old manifest pointing at half-overwritten blocks.
        manifest_path.unlink()

    keys = km.load_xml_keys()
    layers = parse_layers(args.layers or "0-7")
    errors = []
    blocks = {}
    analysis = {"keymaps": {}}

    def do_block(name, op, size, sub=0, chunks=False, **meta):
        print(f"[*] {name:<14} read 0x{0x80 | op:02X} sub {sub} size {size} ...")
        body, error = read_block(op, size, sub, chunks)
        if body is None:
            errors.append({"block": name, "error": error})
            print(f"[!] {name}: {error}")
            return None
        filename = f"{name}.bin"
        (out_dir / filename).write_bytes(body)
        write_op = None if op == INFO_OP else op
        blocks[name] = block_record(
            0x80 | op, write_op, sub, size, filename, body, **meta
        )
        print(f"[+] {name}: {size} bytes -> {filename}")
        return body

    info = do_block(
        "info", INFO_OP, INFO_SIZE, 1, restore="read-only",
        extra={"hex": None},
    )
    if info is not None:
        blocks["info"]["hex"] = info.hex(" ")

    lighting = do_block("lighting", LIGHTING_OP, layout.LIGHTING_SIZE,
                        restore="supported")
    if lighting is not None and len(lighting) > layout.MODE_BODY_OFF:
        mode = lighting[layout.MODE_BODY_OFF]
        analysis["lighting"] = {
            "mode": f"0x{mode:02X}",
            "mode_name": layout.mode_name(mode),
        }

    do_block("colour", COLOUR_OP, layout.COLOUR_SIZE, restore="supported")

    for layer in layers:
        name = f"keymap-layer{layer}"
        body = do_block(
            name, KEYMAP_OP, KEYMAP_SIZE, layer,
            restore="layer-0-confirmed" if layer == 0 else "layer-write-unverified",
        )
        if body is not None:
            report = km.keymap_report(body, keys)
            analysis["keymaps"][str(layer)] = {
                "esc_ok": report["esc_ok"],
                "matched": report["matched"],
                "checked": report["checked"],
                "plausible": report["plausible"],
                "looks_like_keymap": report["looks_like_keymap"],
            }

    if args.macro_size:
        do_block(
            "macros", MACRO_OP, args.macro_size, 0, chunks=True,
            restore="blocked-unverified",
        )

    others_end = dev.other_writers()
    if others_end:
        print(
            "[!] Another writer appeared during capture; snapshot may be moving.",
            file=sys.stderr,
        )

    manifest = {
        "schema": SNAPSHOT_SCHEMA,
        "kind": SNAPSHOT_KIND,
        "read_only": True,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "device": {
            "vid": f"0x{dev.VENDOR_ID:04X}",
            "pid": f"0x{dev.PRODUCT_ID:04X}",
            "interface": 1,
        },
        "clean": not (others or others_end),
        "other_writers": [{"pid": pid, "command": cmd} for pid, cmd in others],
        "other_writers_end": [
            {"pid": pid, "command": cmd} for pid, cmd in others_end
        ],
        "capture": {
            "layers": layers,
            "macro_size": args.macro_size,
        },
        "analysis": analysis,
        "blocks": blocks,
        "errors": errors,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"\n[+] Snapshot: {manifest_path}")
    print(f"    blocks: {len(blocks)}, errors: {len(errors)}, "
          f"clean: {manifest['clean']}")
    if analysis.get("lighting"):
        light = analysis["lighting"]
        print(f"    lighting: {light['mode']} ({light['mode_name']})")
    for layer, report in analysis["keymaps"].items():
        print(
            f"    keymap layer {layer}: Esc(1)="
            f"{'OK' if report['esc_ok'] else 'bad'}, "
            f"XML {report['matched']}/{report['checked']}, "
            f"looks like keymap={report['looks_like_keymap']}"
        )
    if errors:
        print("[!] Snapshot is incomplete; see the errors above.", file=sys.stderr)
        return 1
    return 0


def load_manifest(directory: Path):
    path = directory / "snapshot.json"
    if not path.exists():
        raise ValueError(f"{directory} has no snapshot.json")
    data = json.loads(path.read_text())
    if data.get("kind") != SNAPSHOT_KIND:
        raise ValueError(f"{path} is not a {SNAPSHOT_KIND} manifest")
    return data


def block_body(directory: Path, entry):
    path = (directory / entry["file"]).resolve()
    if path.parent != directory.resolve():
        raise ValueError(f"block file escapes the snapshot: {entry['file']}")
    if not path.exists():
        raise ValueError(f"missing block file: {path}")
    body = path.read_bytes()
    if len(body) != entry["size"]:
        raise ValueError(
            f"{path}: size {len(body)}, manifest says {entry['size']}"
        )
    return body


def first_diffs(before: bytes, after: bytes, limit=8):
    offsets = []
    for index, (old, new) in enumerate(zip(before, after)):
        if old != new:
            offsets.append((index, old, new))
            if len(offsets) >= limit:
                break
    return offsets


def diff_command(args, parser):
    try:
        before_dir = Path(args.diff[0])
        after_dir = Path(args.diff[1])
        before = load_manifest(before_dir)
        after = load_manifest(after_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(f"before: {before_dir}  ({before.get('created', '?')})")
    print(f"after:  {after_dir}  ({after.get('created', '?')})")
    print(f"device: {before.get('device')} -> {after.get('device')}")
    print()

    before_blocks = before.get("blocks", {})
    after_blocks = after.get("blocks", {})
    names = sorted(set(before_blocks) | set(after_blocks))
    changed = 0
    identical = 0
    errors = 0

    for name in names:
        old_entry = before_blocks.get(name)
        new_entry = after_blocks.get(name)
        if old_entry is None:
            print(f"[+] only after:  {name} ({new_entry.get('size')} B)")
            changed += 1
            continue
        if new_entry is None:
            print(f"[-] only before: {name} ({old_entry.get('size')} B)")
            changed += 1
            continue
        try:
            old_body = block_body(before_dir, old_entry)
            new_body = block_body(after_dir, new_entry)
        except (OSError, ValueError) as exc:
            print(f"[!] {name}: {exc}")
            errors += 1
            continue

        if old_body == new_body:
            identical += 1
            print(f"[=] {name}: unchanged ({len(old_body)} B)")
            continue

        changed += 1
        count = sum(1 for a, b in zip(old_body, new_body) if a != b)
        count += abs(len(old_body) - len(new_body))
        print(
            f"[~] {name}: {count} byte(s) differ "
            f"({len(old_body)} -> {len(new_body)} B)"
        )
        for offset, old, new in first_diffs(old_body, new_body):
            print(f"      offset 0x{offset:04X}: {old:02X} -> {new:02X}")
        if name == "lighting" and len(old_body) > layout.MODE_BODY_OFF \
                and len(new_body) > layout.MODE_BODY_OFF:
            old_mode = old_body[layout.MODE_BODY_OFF]
            new_mode = new_body[layout.MODE_BODY_OFF]
            if old_mode != new_mode:
                print(
                    f"      mode: 0x{old_mode:02X} ({layout.mode_name(old_mode)})"
                    f" -> 0x{new_mode:02X} ({layout.mode_name(new_mode)})"
                )
        if name.startswith("keymap-layer") and len(old_body) % 4 == 0 \
                and len(new_body) == len(old_body):
            for cell in range(len(old_body) // 4):
                old_cell = old_body[cell * 4:cell * 4 + 4]
                new_cell = new_body[cell * 4:cell * 4 + 4]
                if old_cell != new_cell:
                    print(
                        f"      cell {cell}: {old_cell.hex(' ')} "
                        f"({km.describe_code(old_cell)}) -> "
                        f"{new_cell.hex(' ')} ({km.describe_code(new_cell)})"
                    )
                    break

    print(f"\n{changed} changed, {identical} unchanged, {errors} error(s)")
    if errors:
        return 2
    return 1 if changed else 0


def save_restore_result(pre_dir: Path, result):
    path = pre_dir / "restore.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"[+] Restore record: {path}")


def restore_command(args, parser):
    try:
        restore_dir = Path(args.restore)
        manifest = load_manifest(restore_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    expected = manifest.get("device", {})
    actual_vid = f"0x{dev.VENDOR_ID:04X}"
    actual_pid = f"0x{dev.PRODUCT_ID:04X}"
    if expected.get("vid") and expected["vid"].lower() != actual_vid.lower():
        parser.error(f"snapshot VID {expected['vid']} does not match {actual_vid}")
    if expected.get("pid") and expected["pid"].lower() != actual_pid.lower():
        parser.error(f"snapshot PID {expected['pid']} does not match {actual_pid}")

    try:
        layers = parse_layers(args.layers or "0")
    except ValueError as exc:
        parser.error(str(exc))
    unverified_layers = [layer for layer in layers if layer > 0]
    if unverified_layers and not args.force and not args.dry_run:
        parser.error(
            "keymap layers above 0 are not hardware-confirmed; pass --force "
            "or use --layers 0"
        )

    blocks = manifest.get("blocks", {})
    items = []
    for name in ("lighting", "colour"):
        entry = blocks.get(name)
        if not entry:
            parser.error(f"snapshot has no {name} block")
        if not entry.get("write_op"):
            parser.error(f"snapshot {name} block has no write opcode")
        items.append((name, int(entry["write_op"], 0), entry.get("sub", 0),
                      entry["size"], entry))
    for layer in layers:
        name = f"keymap-layer{layer}"
        entry = blocks.get(name)
        if not entry:
            parser.error(f"snapshot has no {name} block")
        if not entry.get("write_op"):
            parser.error(f"snapshot {name} block has no write opcode")
        items.append((name, int(entry["write_op"], 0), entry.get("sub", layer),
                      entry["size"], entry))

    macro_note = None
    macro_entry = blocks.get("macros")
    if macro_entry:
        try:
            macro_body = block_body(restore_dir, macro_entry)
            nonzero = sum(1 for byte in macro_body if byte)
            macro_note = (
                "empty; not written"
                if nonzero == 0
                else f"{nonzero} non-zero byte(s); not written"
            )
        except (OSError, ValueError) as exc:
            macro_note = f"could not inspect: {exc}"

    all_layers = sorted(
        int(name.split("layer", 1)[1])
        for name in blocks
        if name.startswith("keymap-layer") and name.split("layer", 1)[1].isdigit()
    )
    skipped_layers = [layer for layer in all_layers if layer not in layers]

    print(f"snapshot: {restore_dir}  ({manifest.get('created', '?')})")
    print("blocks to write:")
    for name, op, sub, size, _entry in items:
        print(f"  {name:<15} write 0x{op:02X} sub {sub}  {size} B -> readback")
    if macro_entry:
        print(f"  macros          skipped ({macro_note})")
    if skipped_layers:
        print("  keymap layers   not selected: "
              + ", ".join(str(layer) for layer in skipped_layers))
    if unverified_layers:
        print("[!] layers above 0 are not hardware-confirmed: "
              + ", ".join(str(layer) for layer in unverified_layers),
              file=sys.stderr)

    if args.dry_run:
        print("\nDry run: no device access and no writes.")
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

    if args.pre_dir:
        pre_dir = Path(args.pre_dir)
        if pre_dir.exists() and any(pre_dir.iterdir()):
            parser.error(f"{pre_dir} is not empty; choose a new --pre-dir")
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        parent = restore_dir.parent
        pre_dir = parent / f"restore-pre-{stamp}"
        serial = 1
        while pre_dir.exists():
            pre_dir = parent / f"restore-pre-{stamp}-{serial}"
            serial += 1
    if pre_dir.resolve() == restore_dir.resolve():
        parser.error("pre-restore snapshot cannot be the snapshot being restored")

    pre_args = argparse.Namespace(
        capture=str(pre_dir),
        layers=args.layers or "0",
        macro_size=0,
        force=False,
    )
    print(f"\n[*] Saving pre-restore snapshot to {pre_dir} ...")
    if capture(pre_args, parser) != 0:
        print("[!] Pre-restore snapshot failed; not writing.", file=sys.stderr)
        return 2

    result = {
        "snapshot": str(restore_dir.resolve()),
        "pre_snapshot": str(pre_dir.resolve()),
        "started": datetime.now().astimezone().isoformat(timespec="seconds"),
        "layers": layers,
        "macro_note": macro_note,
        "skipped_keymap_layers": skipped_layers,
        "blocks": [],
        "verified": False,
        "failed": None,
    }

    for name, op, sub, size, entry in items:
        try:
            body = block_body(restore_dir, entry)
        except (OSError, ValueError) as exc:
            result["failed"] = name
            result["error"] = str(exc)
            save_restore_result(pre_dir, result)
            return 1

        print(f"[*] writing {name}: 0x{op:02X} sub {sub}, {size} bytes ...")
        try:
            written = dev.write_body(op, body, sub=sub)
        except OSError as exc:
            result["failed"] = name
            result["error"] = str(exc)
            save_restore_result(pre_dir, result)
            print(f"[!] {name} write failed: {exc}", file=sys.stderr)
            return 1
        print(f"    write accepted ({written} bytes transferred)")

        time.sleep(0.10)
        post = None
        last_error = None
        for _ in range(3):
            try:
                post = bytes(dev.read_body(op, size, sub))
            except OSError as exc:
                post = None
                last_error = exc
            if post == body:
                break
            time.sleep(0.10)

        if post is not None:
            post_path = pre_dir / f"post-{name}.bin"
            post_path.write_bytes(post)
            post_file = str(post_path)
        else:
            post_file = None

        if post != body:
            result["failed"] = name
            result["error"] = (
                f"readback failed: {last_error}" if post is None
                else "readback differs from snapshot"
            )
            result["post_file"] = post_file
            save_restore_result(pre_dir, result)
            print(f"[!] {name} readback did not match the snapshot.", file=sys.stderr)
            if post is not None:
                for offset, old, new in first_diffs(body, post):
                    print(
                        f"      offset 0x{offset:04X}: {old:02X} -> {new:02X}",
                        file=sys.stderr,
                    )
            return 1

        print(f"    readback matches ({size} bytes)")
        result["blocks"].append(
            {
                "name": name,
                "write_op": f"0x{op:02X}",
                "sub": sub,
                "size": size,
                "post_file": post_file,
                "verified": True,
            }
        )

    result["verified"] = True
    result["finished"] = datetime.now().astimezone().isoformat(timespec="seconds")
    save_restore_result(pre_dir, result)
    print(f"\n[+] Restore verified: {len(result['blocks'])} block(s).")
    if macro_note:
        print(f"    macros skipped: {macro_note}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="K61SE state snapshot capture/diff, plus an explicit "
                    "golden-state restore. Capture and diff are read-only; "
                    "--restore saves a pre-snapshot and verifies every write.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:", 1)[1] if "Examples:" in __doc__ else None,
    )
    parser.add_argument("--capture", metavar="DIR",
                        help="capture a new snapshot into DIR")
    parser.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"),
                        help="offline diff of two snapshot directories")
    parser.add_argument("--restore", metavar="DIR",
                        help="write supported blocks from a snapshot")
    parser.add_argument("--layers",
                        help="keymap layers (capture default: 0-7; restore default: 0)")
    parser.add_argument("--macro-size", type=int, default=MACRO_MAX,
                        help=f"macro bytes to capture, 0 to skip "
                             f"(default: {MACRO_MAX})")
    parser.add_argument("--pre-dir", metavar="DIR",
                        help="pre-restore snapshot directory (default: "
                             "restore-pre-TIMESTAMP next to the snapshot)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the restore plan without touching the device")
    parser.add_argument("--yes", action="store_true",
                        help="confirm writing a restore")
    parser.add_argument("--force", action="store_true",
                        help="allow unverified layers or another writer open")
    args = parser.parse_args(argv)

    modes = [bool(args.capture), bool(args.diff), bool(args.restore)]
    if sum(modes) != 1:
        parser.error(
            "choose exactly one of --capture DIR, --diff BEFORE AFTER, "
            "or --restore DIR"
        )
    if args.macro_size < 0 or args.macro_size > MACRO_MAX:
        parser.error(f"--macro-size must be 0..{MACRO_MAX}")
    if args.layers:
        try:
            parse_layers(args.layers)
        except ValueError as exc:
            parser.error(str(exc))
    if args.dry_run and not args.restore:
        parser.error("--dry-run is only for --restore")
    if args.yes and not args.restore:
        parser.error("--yes is only for --restore")
    if args.pre_dir and not args.restore:
        parser.error("--pre-dir is only for --restore")

    if args.capture:
        return capture(args, parser)
    if args.diff:
        return diff_command(args, parser)
    return restore_command(args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
