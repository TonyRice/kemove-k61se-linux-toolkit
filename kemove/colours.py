"""Colour-block codecs for the KEMOVE families.

The K61SE colour block stores one RGB triple per effect record (the remaining
bytes are untouched).  The K87 stores seven RGB triples per record, which is a
small palette.  Keeping the two encodings separate prevents a K61SE write from
being reused accidentally on a K87.
"""
from __future__ import annotations

from typing import Iterable, Sequence

RGB = tuple[int, int, int]

SINGLE_RECORD_STRIDE = 21
PALETTE7_RECORD_STRIDE = 21
PALETTE7_COLOURS = 7
PALETTE7_BLOCK_RECORDS = 20


def _check_rgb(colour: Sequence[int]) -> RGB:
    if len(colour) != 3:
        raise ValueError(f"RGB colour must have 3 components, got {len(colour)}")
    values = tuple(int(component) for component in colour)
    if any(component < 0 or component > 255 for component in values):
        raise ValueError(f"RGB component out of range: {values}")
    return values  # type: ignore[return-value]


def decode_palette7_record(record: bytes) -> tuple[RGB, ...]:
    """Decode one K87 21-byte record into seven RGB triples."""
    if len(record) != PALETTE7_RECORD_STRIDE:
        raise ValueError(
            f"K87 colour record must be {PALETTE7_RECORD_STRIDE} bytes, got {len(record)}"
        )
    return tuple(
        (record[i * 3], record[i * 3 + 1], record[i * 3 + 2])
        for i in range(PALETTE7_COLOURS)
    )


def encode_palette7_record(colours: Iterable[Sequence[int]]) -> bytes:
    """Encode seven RGB triples into one K87 21-byte record."""
    entries = [_check_rgb(colour) for colour in colours]
    if len(entries) != PALETTE7_COLOURS:
        raise ValueError(f"K87 colour record needs {PALETTE7_COLOURS} colours, got {len(entries)}")
    return bytes(component for colour in entries for component in colour)


def decode_palette7_block(body: bytes) -> tuple[tuple[RGB, ...], ...]:
    """Decode the full 420-byte K87 colour block into 20 records."""
    if len(body) != PALETTE7_RECORD_STRIDE * PALETTE7_BLOCK_RECORDS:
        raise ValueError(
            f"K87 colour block must be {PALETTE7_RECORD_STRIDE * PALETTE7_BLOCK_RECORDS} bytes, "
            f"got {len(body)}"
        )
    return tuple(
        decode_palette7_record(body[i * PALETTE7_RECORD_STRIDE : (i + 1) * PALETTE7_RECORD_STRIDE])
        for i in range(PALETTE7_BLOCK_RECORDS)
    )


def encode_palette7_block(records: Iterable[Iterable[Sequence[int]]]) -> bytes:
    """Encode 20 seven-colour records into the K87 420-byte colour block."""
    encoded = [encode_palette7_record(record) for record in records]
    if len(encoded) != PALETTE7_BLOCK_RECORDS:
        raise ValueError(
            f"K87 colour block needs {PALETTE7_BLOCK_RECORDS} records, got {len(encoded)}"
        )
    return b"".join(encoded)
