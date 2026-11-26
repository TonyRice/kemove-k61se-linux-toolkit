"""Golden evidence for the K87 side-light/light-piping fields."""
from __future__ import annotations

import unittest
from pathlib import Path

from kemove.lighting import lighting_state

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "evidence" / "captures"


class SideLightTests(unittest.TestCase):
    def _state(self, name: str):
        body = (BASE / name / "lighting.bin").read_bytes()
        return lighting_state(body)

    def test_side_mode_cycles_with_fn_tab(self) -> None:
        base = self._state("k87-side-0")
        one = self._state("k87-side-1")
        two = self._state("k87-side-2")
        self.assertEqual((base.side.flag, base.side.mode, base.side.colour), (1, 1, 0))
        self.assertEqual((one.side.flag, one.side.mode, one.side.colour), (0, 2, 0))
        self.assertEqual((two.side.flag, two.side.mode, two.side.colour), (0, 3, 0))

    def test_side_colour_changes_with_fn_capslock(self) -> None:
        before = self._state("k87-side-2")
        after = self._state("k87-side-color")
        self.assertEqual((before.side.mode, before.side.colour), (3, 0))
        self.assertEqual((after.side.mode, after.side.colour), (3, 1))

    def test_colour_palette_block_is_unchanged_by_side_light(self) -> None:
        base = (BASE / "k87-side-0" / "colour.bin").read_bytes()
        for name in ("k87-side-1", "k87-side-2", "k87-side-color"):
            with self.subTest(name=name):
                self.assertEqual((BASE / name / "colour.bin").read_bytes(), base)


if __name__ == "__main__":
    unittest.main()
