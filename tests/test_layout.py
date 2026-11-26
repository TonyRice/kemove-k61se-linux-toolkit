"""Offline tests for the shared layout loader."""
from __future__ import annotations

import unittest
from pathlib import Path

from kemove.layout import load_compat, load_layout, normalize_xml

ROOT = Path(__file__).resolve().parent.parent
K61 = ROOT / "app" / "K61SE-keyboard.xml"
K87 = ROOT / "app" / "layouts" / "K87-1980-keyboard.xml"


class LayoutTests(unittest.TestCase):
    def test_k61_layout(self) -> None:
        keys = load_layout(K61)
        self.assertEqual(len(keys), 61)
        self.assertEqual(max(keys), 83)
        self.assertEqual(keys[1][0].name, "Esc")

    def test_k87_layout_parses_malformed_vendor_xml(self) -> None:
        keys = load_layout(K87)
        self.assertEqual(len(keys), 87)
        self.assertEqual(max(keys), 101)
        self.assertEqual(keys[0][0].name, "Esc")
        self.assertEqual(keys[59][0].name, "Fn")

    def test_compat_shape_matches_legacy_loader(self) -> None:
        keys = load_compat(K87)
        self.assertIsInstance(keys[0], list)
        self.assertEqual(keys[0][0]["name"], "Esc")
        self.assertIn("row_col", keys[0][0])

    def test_normalize_inserts_missing_attribute_space(self) -> None:
        broken = '<menu macro="1"custom_light="0" music="1" />'
        fixed = normalize_xml(broken)
        self.assertIn('"1" custom_light=', fixed)


if __name__ == "__main__":
    unittest.main()
