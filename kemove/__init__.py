"""Shared host-side code for KEMOVE and AULA keyboards."""

from .colours import (
    PALETTE7_BLOCK_RECORDS,
    PALETTE7_COLOURS,
    PALETTE7_RECORD_STRIDE,
    decode_palette7_block,
    decode_palette7_record,
    encode_palette7_block,
    encode_palette7_record,
)
from .devices import (
    AULA_VID,
    CDRV3632,
    G5KB,
    KEMOVE_VID,
    UNKNOWN,
    DeviceProfile,
    PROFILES,
    describe,
    identify,
    profile_for_key,
)

__all__ = [
    "AULA_VID",
    "CDRV3632",
    "G5KB",
    "KEMOVE_VID",
    "PALETTE7_BLOCK_RECORDS",
    "PALETTE7_COLOURS",
    "PALETTE7_RECORD_STRIDE",
    "UNKNOWN",
    "DeviceProfile",
    "PROFILES",
    "decode_palette7_block",
    "decode_palette7_record",
    "describe",
    "encode_palette7_block",
    "encode_palette7_record",
    "identify",
    "profile_for_key",
]
