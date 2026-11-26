"""Read-only HID discovery for the device-profile registry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .devices import DeviceProfile, identify, profile_for_key


@dataclass(frozen=True)
class DeviceMatch:
    profile: DeviceProfile
    info: dict[str, Any]
    path: bytes

    @property
    def product(self) -> str:
        return str(self.info.get("product_string") or "")

    def label(self) -> str:
        return f"{self.profile.name} ({self.product or self.profile.key})"


def enumerate_known() -> list[DeviceMatch]:
    """Return every connected HID interface that matches a known profile."""
    try:
        import hid
    except ImportError as exc:  # pragma: no cover - host dependency
        raise RuntimeError("python hidapi is not installed (python3 -m pip install hid)") from exc

    matches: list[DeviceMatch] = []
    seen: set[bytes] = set()
    for info in hid.enumerate():
        profile = identify(info)
        if profile is None:
            continue
        path = info.get("path")
        if not isinstance(path, (bytes, bytearray)):
            continue
        path = bytes(path)
        if path in seen:
            continue
        seen.add(path)
        matches.append(DeviceMatch(profile=profile, info=dict(info), path=path))
    return matches


def find(profile: str | DeviceProfile | None = None) -> DeviceMatch:
    """Find one connected device, optionally restricted to a profile key."""
    wanted: DeviceProfile | None = None
    if isinstance(profile, DeviceProfile):
        wanted = profile
    elif isinstance(profile, str):
        wanted = profile_for_key(profile)
        if wanted is None:
            raise RuntimeError(f"unknown device profile: {profile}")

    matches = enumerate_known()
    if wanted is not None:
        matches = [match for match in matches if match.profile.key == wanted.key]

    if not matches:
        if wanted is not None:
            raise RuntimeError(f"{wanted.name} not found")
        raise RuntimeError(
            "no recognised KEMOVE/AULA keyboard found; run `python3 -m kemove.probe`"
        )
    if len(matches) > 1:
        names = ", ".join(match.label() for match in matches)
        raise RuntimeError(
            f"multiple recognised keyboards found ({names}); select one with --profile"
        )
    return matches[0]


def find_all() -> list[DeviceMatch]:
    return enumerate_known()
