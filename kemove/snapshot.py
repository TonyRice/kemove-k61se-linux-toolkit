"""Snapshot manifest loading and offline block comparison.

This is the profile-aware core for the existing ``k61se_snapshot.py`` CLI.  It
does not open a device: it validates a snapshot directory, reads its block
files, and compares two snapshots.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .devices import DeviceProfile, profile_for_key

SNAPSHOT_KIND = "k61se-snapshot"
SNAPSHOT_SCHEMA = 1


@dataclass(frozen=True)
class SnapshotBlock:
    name: str
    file: str
    size: int
    read_op: int | None
    write_op: int | None
    sub: int
    sha256: str
    restore: str

    @classmethod
    def from_manifest(cls, name: str, entry: dict[str, Any]) -> "SnapshotBlock":
        def op(value: Any) -> int | None:
            if value is None:
                return None
            return int(value, 0) if isinstance(value, str) else int(value)

        return cls(
            name=name,
            file=str(entry["file"]),
            size=int(entry["size"]),
            read_op=op(entry.get("read_op")),
            write_op=op(entry.get("write_op")),
            sub=int(entry.get("sub", 0)),
            sha256=str(entry.get("sha256", "")),
            restore=str(entry.get("restore", "")),
        )


@dataclass(frozen=True)
class BlockDiff:
    name: str
    before_size: int
    after_size: int
    changed_bytes: int
    offsets: tuple[tuple[int, int, int], ...]


@dataclass
class Snapshot:
    directory: Path
    manifest: dict[str, Any]
    profile: DeviceProfile | None

    @classmethod
    def load(cls, directory: Path | str) -> "Snapshot":
        directory = Path(directory)
        manifest_path = directory / "snapshot.json"
        if not manifest_path.exists():
            raise ValueError(f"{directory} has no snapshot.json")
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("kind") != SNAPSHOT_KIND:
            raise ValueError(f"{manifest_path} is not a {SNAPSHOT_KIND} manifest")

        profile = None
        device = manifest.get("device", {})
        profile_key = manifest.get("profile") or device.get("profile")
        if profile_key:
            profile = profile_for_key(str(profile_key))
        if profile is None:
            vid = str(device.get("vid", "")).lower()
            pid = str(device.get("pid", "")).lower()
            # Legacy snapshots predate the profile field.  K61SE is the only
            # safe interpretation for the old 258A:010C fixture.
            if vid == "0x258a" and pid == "0x010c":
                profile = profile_for_key("k61se")

        return cls(directory=directory, manifest=manifest, profile=profile)

    @property
    def blocks(self) -> dict[str, SnapshotBlock]:
        return {
            name: SnapshotBlock.from_manifest(name, entry)
            for name, entry in self.manifest.get("blocks", {}).items()
        }

    def block(self, name: str) -> bytes:
        block = self.blocks.get(name)
        if block is None:
            raise KeyError(f"snapshot has no block {name!r}")
        path = (self.directory / block.file).resolve()
        if path.parent != self.directory.resolve():
            raise ValueError(f"block file escapes the snapshot: {block.file}")
        if not path.exists():
            raise FileNotFoundError(f"missing block file: {path}")
        body = path.read_bytes()
        if len(body) != block.size:
            raise ValueError(f"{path}: size {len(body)}, manifest says {block.size}")
        if block.sha256 and hashlib.sha256(body).hexdigest() != block.sha256:
            raise ValueError(f"{path}: sha256 does not match the manifest")
        return body

    def diff(self, other: "Snapshot", limit: int = 8) -> list[BlockDiff]:
        before = self.blocks
        after = other.blocks
        result: list[BlockDiff] = []
        for name in sorted(set(before) | set(after)):
            old = self._optional_block(self.directory, before, name)
            new = self._optional_block(other.directory, after, name)
            if old == new:
                continue
            if old is None or new is None:
                result.append(
                    BlockDiff(
                        name=name,
                        before_size=len(old) if old is not None else 0,
                        after_size=len(new) if new is not None else 0,
                        changed_bytes=max(len(old or b""), len(new or b"")),
                        offsets=(),
                    )
                )
                continue
            offsets = tuple(
                (offset, a, b)
                for offset, (a, b) in enumerate(zip(old, new))
                if a != b
            )
            changed = len(offsets) + abs(len(old) - len(new))
            result.append(
                BlockDiff(
                    name=name,
                    before_size=len(old),
                    after_size=len(new),
                    changed_bytes=changed,
                    offsets=offsets[:limit],
                )
            )
        return result

    def _optional_block(
        self, directory: Path, blocks: dict[str, SnapshotBlock], name: str
    ) -> bytes | None:
        if name not in blocks:
            return None
        path = (directory / blocks[name].file).resolve()
        if not path.exists():
            return None
        return path.read_bytes()
