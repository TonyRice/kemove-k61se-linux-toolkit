"""Profile-aware lighting and colour block helpers.

The K61SE and K87 share the 128-byte lighting block shape.  Their colour blocks
are both 420 bytes, but K61SE stores one RGB triple per 21-byte record while
K87 stores seven RGB triples (a palette) per record.  Callers select the format
from the device profile.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .colours import (
    decode_palette7_block,
    decode_palette7_record,
    encode_palette7_record,
)

LIGHTING_SIZE = 128
COLOUR_SIZE = 420
MODE_OFFSET = 0x0A
SIDE_FLAG_OFFSET = 0x0E
SIDE_MODE_OFFSET = 0x12
SIDE_COLOUR_OFFSET = 0x13
PARAMS_OFF = 0x3A
PARAMS_END = 0x7E
LIGHTING_TRAILER = b"\x5a\xa5"
COLOUR_RECORD_STRIDE = 21
COLOUR_RECORDS = COLOUR_SIZE // COLOUR_RECORD_STRIDE

SINGLE = "single"
PALETTE7 = "palette7"
COLOUR_FORMATS = (SINGLE, PALETTE7)

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class SideLightState:
    flag: int
    mode: int
    colour: int


@dataclass(frozen=True)
class LightingState:
    mode: int
    brightness: int | None
    speed_level: int | None
    rainbow: bool | None
    side: SideLightState


def colour_format_for_profile(profile) -> str:
    """Return the colour-record format declared by a DeviceProfile."""
    return getattr(profile, "colour_format", SINGLE)


def validate_lighting(body: bytes) -> None:
    if len(body) != LIGHTING_SIZE:
        raise ValueError(f"lighting block must be {LIGHTING_SIZE} bytes, got {len(body)}")
    if body[PARAMS_END : PARAMS_END + 2] != LIGHTING_TRAILER:
        raise ValueError("lighting block is missing the 5a a5 trailer")


def validate_colour(body: bytes) -> None:
    if len(body) != COLOUR_SIZE:
        raise ValueError(f"colour block must be {COLOUR_SIZE} bytes, got {len(body)}")


def effect_slot_offset(mode: int) -> int | None:
    """Offset of the two-byte parameter slot for a 1-based effect id."""
    if not 1 <= mode <= 34:
        return None
    offset = PARAMS_OFF + (mode - 1) * 2
    return offset if offset + 1 < PARAMS_END else None


def slot_bytes(body: bytes, mode: int) -> tuple[int, int] | None:
    offset = effect_slot_offset(mode)
    if offset is None or offset + 1 >= len(body):
        return None
    return body[offset], body[offset + 1]


def edit_lighting(
    body: bytes,
    *,
    mode: int | None = None,
    brightness: int | None = None,
    speed: int | None = None,
    rainbow: bool | None = None,
    side_flag: int | None = None,
    side_mode: int | None = None,
    side_colour: int | None = None,
) -> bytes:
    """Return a copy of a lighting block with the requested fields changed.

    This is a pure operation; it never writes to a device.  The brightness
    range is deliberately broad (0-9) because the K61SE and K87 tables differ;
    the caller's profile/CLI decides the safe range for a test.
    """
    validate_lighting(body)
    out = bytearray(body)

    if side_flag is not None:
        if int(side_flag) not in (0, 1):
            raise ValueError(f"side flag must be 0 or 1, got {side_flag}")
        out[SIDE_FLAG_OFFSET] = int(side_flag)
    if side_mode is not None:
        if not 0 <= int(side_mode) <= 255:
            raise ValueError(f"side mode out of range: {side_mode}")
        out[SIDE_MODE_OFFSET] = int(side_mode)
    if side_colour is not None:
        if not 0 <= int(side_colour) <= 255:
            raise ValueError(f"side colour out of range: {side_colour}")
        out[SIDE_COLOUR_OFFSET] = int(side_colour)

    active_mode = out[MODE_OFFSET] if mode is None else int(mode)
    if not 0 <= active_mode <= 255:
        raise ValueError(f"mode out of range: {active_mode}")
    if mode is not None:
        out[MODE_OFFSET] = active_mode

    slot = effect_slot_offset(active_mode)
    if slot is None:
        if any(value is not None for value in (brightness, speed, rainbow)):
            raise ValueError(f"mode 0x{active_mode:02X} has no parameter slot")
        return bytes(out)

    if brightness is not None:
        if not 0 <= int(brightness) <= 9:
            raise ValueError(f"brightness out of range: {brightness}")
        out[slot] = int(brightness)

    if speed is not None and not 1 <= int(speed) <= 5:
        raise ValueError(f"speed out of range: {speed}")

    low = out[slot + 1] & 0x0F
    if rainbow is not None:
        low = 0x07 if rainbow else 0x00
    if speed is not None or rainbow is not None:
        level = (out[slot + 1] >> 4) if speed is None else int(speed)
        out[slot + 1] = (level << 4) | low

    return bytes(out)


def lighting_state(body: bytes) -> LightingState:
    validate_lighting(body)
    mode = body[MODE_OFFSET]
    side = SideLightState(
        flag=body[SIDE_FLAG_OFFSET],
        mode=body[SIDE_MODE_OFFSET],
        colour=body[SIDE_COLOUR_OFFSET],
    )
    slot = slot_bytes(body, mode)
    if slot is None:
        return LightingState(
            mode=mode,
            brightness=None,
            speed_level=None,
            rainbow=None,
            side=side,
        )
    brightness, speed_byte = slot
    return LightingState(
        mode=mode,
        brightness=brightness,
        speed_level=(speed_byte >> 4) & 0x0F,
        rainbow=(speed_byte & 0x0F) == 0x07,
        side=side,
    )


def decode_single_record(record: bytes) -> RGB:
    """K61SE record: green, red, blue, then 18 untouched bytes."""
    if len(record) != COLOUR_RECORD_STRIDE:
        raise ValueError(f"single colour record must be {COLOUR_RECORD_STRIDE} bytes")
    return record[1], record[0], record[2]


def encode_single_record(colour: Sequence[int]) -> bytes:
    """Encode a K61SE single-colour record (green, red, blue, padding)."""
    if len(colour) != 3:
        raise ValueError("single colour needs three components")
    r, g, b = (int(c) for c in colour)
    if any(c < 0 or c > 255 for c in (r, g, b)):
        raise ValueError("RGB component out of range")
    return bytes([g, r, b]) + bytes(COLOUR_RECORD_STRIDE - 3)


def decode_colour_record(record: bytes, colour_format: str) -> RGB | tuple[RGB, ...]:
    if colour_format == SINGLE:
        return decode_single_record(record)
    if colour_format == PALETTE7:
        return decode_palette7_record(record)
    raise ValueError(f"unknown colour format: {colour_format}")


def encode_colour_record(colour, colour_format: str) -> bytes:
    if colour_format == SINGLE:
        return encode_single_record(colour)
    if colour_format == PALETTE7:
        return encode_palette7_record(colour)
    raise ValueError(f"unknown colour format: {colour_format}")


def decode_colour_block(body: bytes, colour_format: str):
    validate_colour(body)
    if colour_format == PALETTE7:
        return decode_palette7_block(body)
    if colour_format != SINGLE:
        raise ValueError(f"unknown colour format: {colour_format}")
    return tuple(
        decode_single_record(body[i * COLOUR_RECORD_STRIDE : (i + 1) * COLOUR_RECORD_STRIDE])
        for i in range(COLOUR_RECORDS)
    )
