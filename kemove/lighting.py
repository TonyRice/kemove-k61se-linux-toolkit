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
class LightingState:
    mode: int
    brightness: int | None
    speed_level: int | None
    rainbow: bool | None


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


def lighting_state(body: bytes) -> LightingState:
    validate_lighting(body)
    mode = body[MODE_OFFSET]
    slot = slot_bytes(body, mode)
    if slot is None:
        return LightingState(mode=mode, brightness=None, speed_level=None, rainbow=None)
    brightness, speed_byte = slot
    return LightingState(
        mode=mode,
        brightness=brightness,
        speed_level=(speed_byte >> 4) & 0x0F,
        rainbow=(speed_byte & 0x0F) == 0x07,
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
