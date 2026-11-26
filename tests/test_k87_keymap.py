"""Golden tests for the K87-1980 keymap readback."""
from __future__ import annotations

import unittest
from pathlib import Path

import k61se_macro as km

ROOT = Path(__file__).resolve().parent.parent
K87_BODY = ROOT / "evidence" / "captures" / "k87-probe" / "keymap.bin"
K61_BODY = ROOT / "evidence" / "captures" / "keymap-reset.bin"
K87_XML = ROOT / "app" / "layouts" / "K87-1980-keyboard.xml"
K61_XML = ROOT / "app" / "K61SE-keyboard.xml"


class K87KeymapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.k87 = K87_BODY.read_bytes()
        self.k61 = K61_BODY.read_bytes()
        self.k87_keys = km.load_xml_keys(K87_XML)
        self.k61_keys = km.load_xml_keys(K61_XML)

    def test_k87_layout_parses_despite_malformed_vendor_xml(self) -> None:
        self.assertEqual(len(self.k87_keys), 87)
        self.assertEqual(max(self.k87_keys), 101)
        self.assertIn(0, self.k87_keys)

    def test_k87_body_is_a_504_byte_keymap(self) -> None:
        self.assertEqual(len(self.k87), 504)
        report = km.keymap_report(self.k87, self.k87_keys)
        self.assertTrue(report["esc_ok"])
        self.assertTrue(report["looks_like_keymap"])
        self.assertEqual(report["checked"], 86)
        self.assertEqual(report["matched"], 86)

    def test_k61se_body_still_validates(self) -> None:
        report = km.keymap_report(self.k61, self.k61_keys)
        self.assertTrue(report["esc_ok"])
        self.assertTrue(report["looks_like_keymap"])
        self.assertEqual(report["checked"], 60)
        self.assertEqual(report["matched"], 60)

    def test_k87_and_k61se_use_different_control_cells(self) -> None:
        # Esc is cell 0 on K87, cell 1 on K61SE.
        self.assertEqual(self.k87[0:4], b"\x00\x00\x00\x29")
        self.assertEqual(self.k61[4:8], b"\x00\x00\x00\x29")

        # Fn, Menu, Right Ctrl move to different key indices.
        self.assertEqual(self.k87[59 * 4 : 59 * 4 + 4], b"\x0d\x00\x00\x00")
        self.assertEqual(self.k87[65 * 4 : 65 * 4 + 4], b"\x00\x00\x00\x65")
        self.assertEqual(self.k87[83 * 4 : 83 * 4 + 4], b"\x00\x10\x00\x00")

        self.assertEqual(self.k61[83 * 4 : 83 * 4 + 4], b"\x0d\x00\x00\x00")
        self.assertEqual(self.k61[59 * 4 : 59 * 4 + 4], b"\x00\x00\x00\x65")
        self.assertEqual(self.k61[65 * 4 : 65 * 4 + 4], b"\x00\x10\x00\x00")

    def test_k87_unused_tail_is_zero(self) -> None:
        self.assertEqual(self.k87[102 * 4 :], bytes(24 * 4))


if __name__ == "__main__":
    unittest.main()
