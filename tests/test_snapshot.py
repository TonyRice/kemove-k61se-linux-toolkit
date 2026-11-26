"""Offline tests for snapshot manifests and block diffs."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from kemove.snapshot import Snapshot

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "evidence" / "captures" / "golden-reset"


class SnapshotTests(unittest.TestCase):
    def test_golden_snapshot_loads_with_legacy_profile(self) -> None:
        snap = Snapshot.load(GOLDEN)
        self.assertIsNotNone(snap.profile)
        assert snap.profile is not None
        self.assertEqual(snap.profile.key, "k61se")
        self.assertEqual(len(snap.blocks), 12)
        self.assertEqual(len(snap.block("lighting")), 128)
        self.assertEqual(len(snap.block("colour")), 420)
        self.assertEqual(len(snap.block("keymap-layer0")), 504)

    def test_snapshot_does_not_diff_against_itself(self) -> None:
        snap = Snapshot.load(GOLDEN)
        self.assertEqual(snap.diff(snap), [])

    def test_snapshot_diff_reports_changed_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "after"
            shutil.copytree(GOLDEN, copy)
            lighting = copy / "lighting.bin"
            body = bytearray(lighting.read_bytes())
            body[0x0A] = 0x01
            lighting.write_bytes(body)
            # Keep the manifest hash stale on purpose: diff() compares bytes,
            # while block() is the strict path that validates hashes.
            before = Snapshot.load(GOLDEN)
            after = Snapshot.load(copy)
            changes = before.diff(after)
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0].name, "lighting")
            self.assertEqual(changes[0].changed_bytes, 1)
            self.assertEqual(changes[0].offsets[0][0], 0x0A)

    def test_block_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "snapshot.json").write_text(
                json.dumps(
                    {
                        "kind": "k61se-snapshot",
                        "blocks": {
                            "bad": {
                                "file": "../outside.bin",
                                "size": 1,
                                "sub": 0,
                                "sha256": "",
                            }
                        },
                    }
                )
            )
            (directory.parent / "outside.bin").write_bytes(b"x")
            snap = Snapshot.load(directory)
            with self.assertRaises(ValueError):
                snap.block("bad")


if __name__ == "__main__":
    unittest.main()
