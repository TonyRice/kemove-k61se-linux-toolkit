"""Device profiles shared by the KEMOVE/AULA host tools.

The vendor app uses one transport implementation for several keyboards.  The
differences that matter to the host tools are the USB identity, the product
string, the transport framing, and the layout/keymap sizes.  Keeping those in
one registry avoids the current situation where every script hard-codes
``258A:010C`` and the K61SE XML.

This module is intentionally dependency-free: it can be imported by the probe,
the CLI tools, tests, and (later) the Rust-side configuration generator.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

# Vendor IDs seen in the KEMOVE/AULA ecosystem.
KEMOVE_VID = 0x258A
AULA_VID = 0x3554

# Transport names used by the protocol modules.
G5KB = "g5kb"          # 520-byte feature report, report id 0x06
CDRV3632 = "cdrv3632"  # 20-byte report 0x13, used by the 2.4G receiver/BT
UNKNOWN = "unknown"


@dataclass(frozen=True)
class DeviceProfile:
    """One physical model and the protocol/layout facts we know about it.

    ``verified`` means the profile has been exercised on hardware.  A profile
    can be present with ``verified=False`` so discovery and read-only probing
    can identify the device without pretending writes are safe.
    """

    key: str
    name: str
    vendor_id: int
    product_id: int
    product_contains: tuple[str, ...] = ()
    interface_number: int | None = 1
    usage_page: int | None = None
    transport: str = UNKNOWN
    report_id: int | None = None
    report_len: int | None = None
    keymap_len: int | None = None
    led_count: int | None = None
    colour_format: str = "single"
    layout: str | None = None
    verified: bool = False
    notes: str = ""

    def matches(self, info: Mapping[str, Any]) -> bool:
        """Return True when an ``hid.enumerate()`` entry fits this profile."""
        if info.get("vendor_id") != self.vendor_id:
            return False
        if info.get("product_id") != self.product_id:
            return False
        if self.interface_number is not None:
            if info.get("interface_number") != self.interface_number:
                return False
        if self.usage_page is not None:
            if info.get("usage_page") != self.usage_page:
                return False

        product = str(info.get("product_string") or "").lower()
        return all(token.lower() in product for token in self.product_contains)

    def score(self, info: Mapping[str, Any]) -> int:
        """Prefer the profile with the most specific product-string match."""
        product = str(info.get("product_string") or "").lower()
        score = sum(len(token) for token in self.product_contains)
        if product == self.name.lower():
            score += 100
        if self.verified:
            score += 1
        return score

    def layout_path(self) -> Path | None:
        if not self.layout:
            return None
        return Path(__file__).resolve().parent.parent / self.layout


# The order is not significant; identify() scores matches.
PROFILES: tuple[DeviceProfile, ...] = (
    DeviceProfile(
        key="k61se",
        name="KEMOVE K61SE",
        vendor_id=KEMOVE_VID,
        product_id=0x010C,
        product_contains=("k61se",),
        interface_number=1,
        transport=G5KB,
        report_id=0x06,
        report_len=520,
        keymap_len=504,
        led_count=126,
        layout="app/K61SE-keyboard.xml",
        verified=True,
        notes="Current verified target. 61 keys, 126-cell keymap block.",
    ),
    DeviceProfile(
        key="k87-1980",
        name="KEMOVE K87-1980",
        vendor_id=KEMOVE_VID,
        product_id=0x010C,
        product_contains=("k87-1980",),
        interface_number=1,
        transport=G5KB,
        report_id=0x06,
        report_len=520,
        keymap_len=504,
        led_count=108,
        colour_format="palette7",
        layout="app/layouts/K87-1980-keyboard.xml",
        verified=False,
        notes=(
            "Same USB identity as K61SE/K68SE. Keymap read verified: 504-byte "
            "body, 126 cells, factory XML matches 86/86. Colour records are "
            "seven RGB triples per effect, not the K61SE single-colour record. "
            "Writes are still unverified."
        ),
    ),
    DeviceProfile(
        key="k87se-1980",
        name="KEMOVE K87SE-1980",
        vendor_id=KEMOVE_VID,
        product_id=0x0164,
        product_contains=("k87se-1980",),
        interface_number=1,
        transport=G5KB,
        report_id=0x06,
        report_len=520,
        led_count=108,
        layout="app/layouts/K87SE-1980-keyboard.xml",
        verified=False,
        notes=(
            "PID 0x0164 is shared with T84SE/T87SE and possibly other SE "
            "models. Transport and keymap size are unverified."
        ),
    ),
    DeviceProfile(
        key="k98-1980",
        name="KEMOVE K98-1980",
        vendor_id=KEMOVE_VID,
        product_id=0x010C,
        product_contains=("k98-1980",),
        interface_number=1,
        transport=G5KB,
        report_id=0x06,
        report_len=520,
        layout="app/layouts/K98-1980-keyboard.xml",
        verified=False,
        notes="Same USB identity as K61SE/K87-1980; unverified.",
    ),
    DeviceProfile(
        key="k98se-1980",
        name="KEMOVE K98SE-1980",
        vendor_id=KEMOVE_VID,
        product_id=0x0164,
        product_contains=("k98se-1980",),
        interface_number=1,
        transport=G5KB,
        report_id=0x06,
        report_len=520,
        layout="app/layouts/K98SE-1980-keyboard.xml",
        verified=False,
        notes="PID 0x0164 family; unverified.",
    ),
    DeviceProfile(
        key="aula-f75-wired",
        name="AULA F75 (USB-C)",
        vendor_id=KEMOVE_VID,
        product_id=0x010C,
        product_contains=("aula", "f75"),
        interface_number=1,
        transport=G5KB,
        report_id=0x06,
        report_len=520,
        keymap_len=512,
        led_count=126,
        verified=False,
        notes=(
            "AULA F75 wired transport documented in ../aula-f75-linux. "
            "Same vendor/product ID as KEMOVE keyboards, so product-string "
            "matching is required."
        ),
    ),
    DeviceProfile(
        key="aula-f75-2.4g",
        name="AULA F75 (2.4G receiver)",
        vendor_id=AULA_VID,
        product_id=0xFA09,
        product_contains=("aula", "f75"),
        interface_number=None,
        usage_page=0xFF02,
        transport=CDRV3632,
        report_id=0x13,
        report_len=20,
        keymap_len=512,
        led_count=126,
        verified=False,
        notes="20-byte report-0x13 transport from ../aula-f75-linux.",
    ),
    DeviceProfile(
        key="aula-f75-bluetooth",
        name="AULA F75 (Bluetooth)",
        vendor_id=AULA_VID,
        product_id=0xFA08,
        product_contains=("aula", "f75"),
        interface_number=None,
        usage_page=0xFF02,
        transport=CDRV3632,
        report_id=0x13,
        report_len=20,
        verified=False,
        notes="Same vendor collection, but AULA's own app does not configure BT.",
    ),
)


def identify(info: Mapping[str, Any]) -> DeviceProfile | None:
    """Return the best matching profile for one ``hid.enumerate()`` entry."""
    matches = [profile for profile in PROFILES if profile.matches(info)]
    if not matches:
        return None
    return max(matches, key=lambda profile: profile.score(info))


def profile_for_key(key: str) -> DeviceProfile | None:
    return next((profile for profile in PROFILES if profile.key == key), None)


def describe(info: Mapping[str, Any]) -> str:
    """Short human-readable identity for an HID entry."""
    profile = identify(info)
    if profile is None:
        return "unrecognised"
    state = "verified" if profile.verified else "provisional"
    return f"{profile.name} ({profile.key}, {state})"
