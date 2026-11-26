#!/usr/bin/env python3
"""Lighting and colour inspection, plus explicit verified writes.

    python3 -m kemove.lighting_cli read
    python3 -m kemove.lighting_cli read --profile k87-1980
    python3 -m kemove.lighting_cli read --save-dir evidence/captures/k87-live

    python3 -m kemove.lighting_cli decode \
        --lighting evidence/captures/k87-probe/lighting.bin \
        --colour evidence/captures/k87-probe/colour.bin \
        --profile k87-1980

    python3 -m kemove.lighting_cli set --profile k87-1980 --brightness 3 --dry-run
    python3 -m kemove.lighting_cli set --profile k87-1980 --brightness 3 --yes --force

`read` and `decode` are read-only.  `set` is an explicit writer: it reads the
current block, shows the planned change, saves pre-write evidence, writes the
128-byte lighting block, and verifies it by reading it back.  Provisional
profiles (currently K87) require `--force`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import k61se_dev as dev
from kemove.devices import profile_for_key
from kemove.discovery import find as find_device
from kemove.lighting import (
    colour_format_for_profile,
    decode_colour_block,
    edit_lighting,
    lighting_state,
)

LIGHTING_OP = 0x04
LIGHTING_SIZE = 128
COLOUR_OP = 0x0A
COLOUR_SIZE = 420


def _profile(args, parser, *, need_device: bool):
    if args.profile:
        profile = profile_for_key(args.profile)
        if profile is None:
            parser.error(f"unknown device profile {args.profile!r}")
        return profile
    if need_device:
        try:
            return find_device().profile
        except RuntimeError as exc:
            parser.error(str(exc))
    return profile_for_key("k61se")


def _read_block(op: int, size: int, profile) -> bytes:
    body = bytes(dev.read_body(op, size, 0, profile=profile))
    if len(body) != size:
        raise OSError(f"read returned {len(body)} bytes, wanted {size}")
    return body


def describe_lighting(body: bytes) -> dict:
    state = lighting_state(body)
    return {
        "mode": state.mode,
        "mode_hex": f"0x{state.mode:02X}",
        "brightness": state.brightness,
        "speed_level": state.speed_level,
        "rainbow": state.rainbow,
    }


def _print_summary(lighting: bytes, colour: bytes, profile) -> None:
    fmt = colour_format_for_profile(profile)
    light = describe_lighting(lighting)
    print(f"profile: {profile.name} ({profile.key}, colour={fmt})")
    print(
        "lighting: "
        f"mode={light['mode_hex']} brightness={light['brightness']} "
        f"speed={light['speed_level']} rainbow={light['rainbow']}"
    )
    records = decode_colour_block(colour, fmt)
    print(f"colour records: {len(records)}")
    for i, record in enumerate(records):
        if fmt == "palette7":
            print(f"  record {i:2d}: " + " ".join("#%02x%02x%02x" % rgb for rgb in record))
        elif any(record):
            print(f"  record {i:2d}: #%02x%02x%02x" % record)


def cmd_read(args, parser) -> int:
    profile = _profile(args, parser, need_device=True)
    lighting = _read_block(LIGHTING_OP, LIGHTING_SIZE, profile)
    colour = _read_block(COLOUR_OP, COLOUR_SIZE, profile)
    _print_summary(lighting, colour, profile)
    if args.save_dir:
        path = Path(args.save_dir)
        path.mkdir(parents=True, exist_ok=True)
        (path / "lighting.bin").write_bytes(lighting)
        (path / "colour.bin").write_bytes(colour)
        print(f"saved: {path / 'lighting.bin'}, {path / 'colour.bin'}")
    return 0


def cmd_decode(args, parser) -> int:
    if not args.lighting or not args.colour:
        parser.error("decode needs --lighting FILE and --colour FILE")
    profile = _profile(args, parser, need_device=False)
    lighting = Path(args.lighting).read_bytes()
    colour = Path(args.colour).read_bytes()
    _print_summary(lighting, colour, profile)
    return 0


def _load_lighting_file(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) == LIGHTING_SIZE:
        return data
    if len(data) >= 8 and data[0] == 0x06 and (data[1] & 0x7F) == LIGHTING_OP:
        size = data[6] | (data[7] << 8)
        if size == LIGHTING_SIZE and len(data) >= 8 + size:
            return data[8 : 8 + size]
    raise ValueError(f"{path} is not a 128-byte lighting body or report-6 frame")


def _save_pre(save_dir: Path, pre: bytes, target: bytes) -> str:
    """Write pre-write evidence before touching the device."""
    save_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    serial = 1
    while (save_dir / f"pre-{stamp}.bin").exists():
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{serial}"
        serial += 1
    (save_dir / f"pre-{stamp}.bin").write_bytes(pre)
    (save_dir / f"write-{stamp}.bin").write_bytes(target)
    return stamp


def _save_result(save_dir: Path, stamp: str, pre: bytes, post: bytes, verified: bool) -> Path:
    (save_dir / f"post-{stamp}.bin").write_bytes(post)
    result = {
        "stamp": stamp,
        "verified": verified,
        "pre": f"pre-{stamp}.bin",
        "write": f"write-{stamp}.bin",
        "post": f"post-{stamp}.bin",
        "changed_offsets": [
            {"offset": i, "before": a, "after": b}
            for i, (a, b) in enumerate(zip(pre, post))
            if a != b
        ],
    }
    path = save_dir / f"result-{stamp}.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    return path


def cmd_set(args, parser) -> int:
    profile = _profile(args, parser, need_device=True)
    modifications = any(
        value is not None
        for value in (args.mode, args.brightness, args.speed, args.rainbow, args.restore)
    )
    if not modifications:
        parser.error("set needs at least one of --mode, --brightness, --speed, --rainbow, --restore")
    if args.restore and any(value is not None for value in (args.mode, args.brightness, args.speed, args.rainbow)):
        parser.error("--restore cannot be combined with --mode/--brightness/--speed/--rainbow")
    if not args.dry_run and not profile.verified and not args.force:
        parser.error(
            f"{profile.name} has a provisional profile; lighting writes are disabled. "
            "Use --dry-run, or pass --force after hardware verification."
        )

    pre = _read_block(LIGHTING_OP, LIGHTING_SIZE, profile)
    if args.restore:
        target = _load_lighting_file(Path(args.restore))
    else:
        rainbow = None
        if args.rainbow is not None:
            rainbow = args.rainbow == "on"
        target = edit_lighting(
            pre,
            mode=args.mode,
            brightness=args.brightness,
            speed=args.speed,
            rainbow=rainbow,
        )

    print(f"profile: {profile.name} ({profile.key})")
    print(f"before: {describe_lighting(pre)}")
    print(f"after:  {describe_lighting(target)}")
    changes = [(i, a, b) for i, (a, b) in enumerate(zip(pre, target)) if a != b]
    print(f"changed bytes: {len(changes)}")
    for offset, before, after in changes[:8]:
        print(f"  offset 0x{offset:02X}: {before:02X} -> {after:02X}")
    if len(changes) > 8:
        print(f"  ... {len(changes) - 8} more")

    if args.dry_run:
        print("Dry run: no device write, no files saved.")
        return 0
    if not args.yes:
        print("Refusing to write without --yes.")
        return 2

    save_dir = Path(args.save_dir) if args.save_dir else Path("evidence/captures/lighting-write")
    stamp = _save_pre(save_dir, pre, target)
    print(f"pre-write evidence: {save_dir / f'pre-{stamp}.bin'}")
    print("Writing lighting block (0x04, 128 bytes, sub=0) ...")
    dev.write_body(LIGHTING_OP, target, sub=0, profile=profile)
    time.sleep(0.10)
    post = _read_block(LIGHTING_OP, LIGHTING_SIZE, profile)
    verified = post == target
    result_path = _save_result(save_dir, stamp, pre, post, verified)
    print(f"readback: {'matches' if verified else 'DIFFERS'}")
    print(f"evidence: {result_path}")
    if not verified:
        for offset, before, after in [(i, a, b) for i, (a, b) in enumerate(zip(target, post)) if a != b][:8]:
            print(f"  offset 0x{offset:02X}: wrote {before:02X}, read {after:02X}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--profile", metavar="KEY", help="device profile key (default: auto-detect)")
    sub = parser.add_subparsers(dest="command", required=True)

    read = sub.add_parser("read", help="read lighting/colour blocks from the device (read-only)")
    read.add_argument("--profile", metavar="KEY", help="device profile key", default=argparse.SUPPRESS)
    read.add_argument("--save-dir", type=Path, help="save the raw blocks here")
    read.set_defaults(func=cmd_read)

    decode = sub.add_parser("decode", help="decode saved lighting/colour blocks")
    decode.add_argument("--profile", metavar="KEY", help="device profile key", default=argparse.SUPPRESS)
    decode.add_argument("--lighting", type=Path, required=True)
    decode.add_argument("--colour", type=Path, required=True)
    decode.set_defaults(func=cmd_decode)

    set_cmd = sub.add_parser("set", help="read-modify-write the lighting block with verification")
    set_cmd.add_argument("--profile", metavar="KEY", help="device profile key", default=argparse.SUPPRESS)
    set_cmd.add_argument("--mode", type=lambda value: int(value, 0), help="effect id")
    set_cmd.add_argument("--brightness", type=int, help="brightness (0-9)")
    set_cmd.add_argument("--speed", type=int, help="speed level (1-5)")
    set_cmd.add_argument("--rainbow", choices=("on", "off"), help="firmware rainbow flag")
    set_cmd.add_argument("--restore", type=Path, help="write a saved 128-byte lighting body")
    set_cmd.add_argument("--save-dir", type=Path, help="evidence directory")
    set_cmd.add_argument("--dry-run", action="store_true", help="show the change without writing")
    set_cmd.add_argument("--yes", action="store_true", help="confirm the write")
    set_cmd.add_argument("--force", action="store_true", help="allow a provisional profile")
    set_cmd.set_defaults(func=cmd_set)

    args = parser.parse_args(argv)
    return args.func(args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
