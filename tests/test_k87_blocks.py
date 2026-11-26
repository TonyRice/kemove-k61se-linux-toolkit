"""Golden checks for the K87 lighting and colour blocks."""
from __future__ import annotations

import unittest
from pathlib import Path

from kemove.colours import (
    PALETTE7_BLOCK_RECORDS,
    PALETTE7_COLOURS,
    decode_palette7_block,
    decode_palette7_record,
    encode_palette7_block,
    encode_palette7_record,
)
from kemove.devices import profile_for_key

ROOT = Path(__file__).resolve().parent.parent
K87_DIR = ROOT / "evidence" / "captures" / "k87-probe"
K61_DIR = ROOT / "evidence" / "captures" / "golden-reset"


class K87BlockTests(unittest.TestCase):
    def test_lighting_block_is_the_same_size_and_trailer(self) -> None:
        k87 = (K87_DIR / "lighting.bin").read_bytes()
        k61 = (K61_DIR / "lighting.bin").read_bytes()
        self.assertEqual(len(k87), 128)
        self.assertEqual(len(k61), 128)
        self.assertEqual(k87[126:128], b"\x5a\xa5")
        self.assertEqual(k61[126:128], b"\x5a\xa5")

    def test_lighting_block_keeps_the_same_parameter_table_shape(self) -> None:
        k87 = (K87_DIR / "lighting.bin").read_bytes()
        # 34 two-byte slots, 0x3A..0x7D, then the 5A A5 trailer.
        self.assertEqual(k87[0x38:0x3A], b"\xff\xff")
        self.assertEqual(k87[0x7E:0x80], b"\x5a\xa5")
        self.assertEqual(len(k87[0x3A:0x7E]), 68)
        # The K87 snapshot is in mode 8; slot 8 is the one that has been moved.
        self.assertEqual(k87[0x0A], 0x08)
        self.assertEqual(k87[0x3A + 2 * 7], 0x04)

    def test_k87_colour_records_are_seven_rgb_triples(self) -> None:
        body = (K87_DIR / "colour.bin").read_bytes()
        self.assertEqual(len(body), 420)
        self.assertEqual(len(body) // 21, 20)

        expected = bytes.fromhex(
            "ff 00 00 00 ff 00 00 00 ff ff ff 00 ff 00 ff 00 ff ff ff ff ff"
        )
        for mode in range(20):
            record = body[mode * 21 : (mode + 1) * 21]
            with self.subTest(mode=mode):
                self.assertEqual(record, expected)
                palette = [tuple(record[i * 3 : i * 3 + 3]) for i in range(7)]
                self.assertEqual(
                    palette,
                    [
                        (255, 0, 0),
                        (0, 255, 0),
                        (0, 0, 255),
                        (255, 255, 0),
                        (255, 0, 255),
                        (0, 255, 255),
                        (255, 255, 255),
                    ],
                )

    def test_k61se_colour_record_is_not_the_palette_layout(self) -> None:
        body = (K61_DIR / "colour.bin").read_bytes()
        self.assertEqual(len(body), 420)
        # K61SE effect 1 is one RGB triple followed by 18 untouched bytes.
        self.assertEqual(body[21:42], b"\x00\xff\x00" + bytes(18))

    def test_palette7_codec_round_trips_every_record(self) -> None:
        body = (K87_DIR / "colour.bin").read_bytes()
        records = decode_palette7_block(body)
        self.assertEqual(len(records), PALETTE7_BLOCK_RECORDS)
        self.assertTrue(all(len(record) == PALETTE7_COLOURS for record in records))
        self.assertEqual(encode_palette7_block(records), body)

    def test_palette7_record_round_trip(self) -> None:
        record = (K87_DIR / "colour.bin").read_bytes()[:21]
        colours = decode_palette7_record(record)
        self.assertEqual(encode_palette7_record(colours), record)
        self.assertEqual(
            colours[0],
            (255, 0, 0),
        )
        self.assertEqual(colours[-1], (255, 255, 255))

    def test_palette7_codec_rejects_wrong_shapes(self) -> None:
        with self.assertRaises(ValueError):
            decode_palette7_record(bytes(20))
        with self.assertRaises(ValueError):
            encode_palette7_record([(0, 0, 0)] * 6)
        with self.assertRaises(ValueError):
            decode_palette7_block(bytes(419))

    def test_profiles_record_the_colour_dialect(self) -> None:
        k87 = profile_for_key("k87-1980")
        k61 = profile_for_key("k61se")
        assert k87 is not None and k61 is not None
        self.assertEqual(k87.colour_format, "palette7")
        self.assertEqual(k61.colour_format, "single")


if __name__ == "__main__":
    unittest.main()
