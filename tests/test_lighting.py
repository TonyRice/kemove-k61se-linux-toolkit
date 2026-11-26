"""Offline tests for profile-aware lighting/colour helpers."""
from __future__ import annotations

import unittest
from pathlib import Path

from kemove.lighting import (
    PALETTE7,
    SINGLE,
    decode_colour_block,
    decode_colour_record,
    edit_lighting,
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

    def test_edit_lighting_changes_only_the_target_slot(self) -> None:
        before = self.k87_light
        after = edit_lighting(before, brightness=3)
        state_before = lighting_state(before)
        state_after = lighting_state(after)
        self.assertEqual(state_before.speed_level, state_after.speed_level)
        self.assertEqual(state_before.rainbow, state_after.rainbow)
        self.assertEqual(state_after.brightness, 3)
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(changed, [0x48])

    def test_edit_lighting_speed_and_rainbow(self) -> None:
        after = edit_lighting(self.k87_light, speed=5, rainbow=False)
        state = lighting_state(after)
        self.assertEqual(state.speed_level, 5)
        self.assertFalse(state.rainbow)
        self.assertEqual(state.brightness, 4)

    def test_edit_lighting_rejects_bad_values(self) -> None:
        with self.assertRaises(ValueError):
            edit_lighting(self.k87_light, brightness=10)
        with self.assertRaises(ValueError):
            edit_lighting(self.k87_light, speed=0)
        with self.assertRaises(ValueError):
            edit_lighting(self.k87_light, mode=0, brightness=1)

    def test_side_light_fields(self) -> None:
        state = lighting_state(self.k87_light)
        self.assertEqual(state.side.flag, 1)
        self.assertEqual(state.side.mode, 1)
        self.assertEqual(state.side.colour, 0)

    def test_edit_side_light_only_changes_side_bytes(self) -> None:
        before = self.k87_light
        after = edit_lighting(before, side_mode=2, side_colour=1, side_flag=0)
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(changed, [0x0E, 0x12, 0x13])
        state = lighting_state(after)
        self.assertEqual(state.side.flag, 0)
        self.assertEqual(state.side.mode, 2)
        self.assertEqual(state.side.colour, 1)


if __name__ == "__main__":
    unittest.main()
