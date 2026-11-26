"""Offline tests for profile-aware lighting/colour helpers."""
from __future__ import annotations

import unittest
from pathlib import Path

from kemove.lighting import (
    PALETTE7,
    SINGLE,
    decode_colour_block,
    decode_colour_record,
    encode_colour_record,
    lighting_state,
    validate_colour,
    validate_lighting,
)

ROOT = Path(__file__).resolve().parent.parent
K87_DIR = ROOT / "evidence" / "captures" / "k87-probe"
K61_DIR = ROOT / "evidence" / "captures" / "golden-reset"


class LightingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.k87_light = (K87_DIR / "lighting.bin").read_bytes()
        self.k61_light = (K61_DIR / "lighting.bin").read_bytes()
        self.k87_colour = (K87_DIR / "colour.bin").read_bytes()
        self.k61_colour = (K61_DIR / "colour.bin").read_bytes()

    def test_both_lighting_blocks_validate(self) -> None:
        validate_lighting(self.k87_light)
        validate_lighting(self.k61_light)
        self.assertEqual(len(self.k87_light), 128)
        self.assertEqual(len(self.k61_light), 128)

    def test_k87_lighting_state(self) -> None:
        state = lighting_state(self.k87_light)
        self.assertEqual(state.mode, 0x08)
        self.assertEqual(state.brightness, 4)
        self.assertEqual(state.speed_level, 3)
        self.assertTrue(state.rainbow)

    def test_k61se_lighting_state(self) -> None:
        state = lighting_state(self.k61_light)
        self.assertEqual(state.mode, 0x0B)
        self.assertEqual(state.brightness, 3)
        self.assertEqual(state.speed_level, 3)
        self.assertTrue(state.rainbow)

    def test_bad_lighting_trailer_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_lighting(bytes(128))
        with self.assertRaises(ValueError):
            validate_lighting(bytes(126) + b"\x00\x00")

    def test_k87_palette_records(self) -> None:
        validate_colour(self.k87_colour)
        records = decode_colour_block(self.k87_colour, PALETTE7)
        self.assertEqual(len(records), 20)
        self.assertEqual(records[0][0], (255, 0, 0))
        self.assertEqual(records[0][-1], (255, 255, 255))
        self.assertEqual(records[0], records[-1])

    def test_k61se_single_records(self) -> None:
        validate_colour(self.k61_colour)
        records = decode_colour_block(self.k61_colour, SINGLE)
        self.assertEqual(len(records), 20)
        self.assertEqual(records[1], (255, 0, 0))  # effect 1
        self.assertEqual(records[0], (0, 0, 0))    # effect 0 is empty

    def test_single_record_round_trip(self) -> None:
        record = encode_colour_record((12, 34, 56), SINGLE)
        self.assertEqual(len(record), 21)
        self.assertEqual(decode_colour_record(record, SINGLE), (12, 34, 56))

    def test_palette_record_round_trip(self) -> None:
        palette = [(i * 10, i, 255 - i) for i in range(7)]
        record = encode_colour_record(palette, PALETTE7)
        self.assertEqual(len(record), 21)
        self.assertEqual(decode_colour_record(record, PALETTE7), tuple(palette))

    def test_unknown_colour_format_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            decode_colour_block(self.k87_colour, "nope")


if __name__ == "__main__":
    unittest.main()
