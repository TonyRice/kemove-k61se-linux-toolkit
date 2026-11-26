"""Vendor layout XML loading.

The vendor files are mostly well-formed, but at least the K87 layout contains a
missing separating space between two attributes (``"1"custom_light=``).  A
tolerant normalisation pass keeps us from carrying a patched copy of a vendor
file in the repository.

`load_layout()` returns typed keys; `load_compat()` returns the legacy
``dict`` shape used by the existing tools.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

MATRIX_CELLS = 126

_MISSING_ATTR_SPACE = re.compile(r'"([A-Za-z_][\w:-]*)=')
_KEY_INDEX = re.compile(r"\d+")


@dataclass(frozen=True)
class Key:
    index: int
    name: str
    desc: str
    code: int
    row_col: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "desc": self.desc,
            "code": self.code,
            "row_col": self.row_col,
        }


def normalize_xml(text: str) -> str:
    """Insert the space the vendor XML sometimes omits between attributes."""
    return _MISSING_ATTR_SPACE.sub(r'" \1=', text)


def load_layout(path: Path | str, cells: int = MATRIX_CELLS) -> dict[int, list[Key]]:
    """Parse a vendor layout XML into ``{key_index: [Key, ...]}``.

    Duplicate labels such as Shift/Ctrl/Alt are preserved as separate entries.
    ``key_index`` is the keymap cell number directly; it is not rebased.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"missing key layout: {path}")
    root = ET.fromstring(normalize_xml(path.read_text(encoding="utf-8", errors="replace")))

    keys: dict[int, list[Key]] = {}
    for elem in root.iter("key"):
        raw_index = elem.get("key_index")
        if raw_index is None:
            continue
        try:
            index = int(raw_index)
        except ValueError:
            continue
        if not 0 <= index < cells:
            continue
        try:
            code = int(elem.get("code", "0"), 0)
        except ValueError:
            code = 0
        keys.setdefault(index, []).append(
            Key(
                index=index,
                name=elem.get("name", "?"),
                desc=elem.get("desc", ""),
                code=code,
                row_col=elem.get("row_col", ""),
            )
        )
    return keys


def load_compat(path: Path | str, cells: int = MATRIX_CELLS) -> dict[int, list[dict[str, object]]]:
    """Legacy shape used by k61se_macro/k61se_keymap."""
    return {
        index: [key.as_dict() for key in entries]
        for index, entries in load_layout(path, cells).items()
    }


def key_names(keys: dict[int, list[Key]]) -> dict[int, list[str]]:
    return {index: [key.name for key in entries] for index, entries in keys.items()}
