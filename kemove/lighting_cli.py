#!/usr/bin/env python3
"""Read-only lighting and colour inspection.

    python3 -m kemove.lighting_cli read
    python3 -m kemove.lighting_cli read --profile k87-1980
    python3 -m kemove.lighting_cli read --save-dir evidence/captures/k87-live

    python3 -m kemove.lighting_cli decode \
        --lighting evidence/captures/k87-probe/lighting.bin \
        --colour evidence/captures/k87-probe/colour.bin \
        --profile k87-1980

This tool never writes a block.  It reads the 128-byte lighting block and the
420-byte colour block, then decodes them using the selected profile's colour
format (K61SE single colour vs K87 palette7).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import k61se_dev as dev
from kemove.devices import profile_for_key
from kemove.discovery import find as find_device
from kemove.lighting import (
    colour_format_for_profile,
    decode_colour_block,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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

    # --profile belongs before the subcommand.
    args = parser.parse_args(argv)
    return args.func(args, parser)


if __name__ == "__main__":
    raise SystemExit(main())
