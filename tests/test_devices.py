"""Offline tests for the device-profile registry."""
from __future__ import annotations

import unittest

from kemove.devices import (
    AULA_VID,
    G5KB,
    KEMOVE_VID,
    PROFILES,
    identify,
    profile_for_key,
)


def info(
    vid: int,
    pid: int,
    product: str,
    *,
    interface: int | None = 1,
    usage_page: int | None = 0xFF00,
) -> dict:
    return {
        "vendor_id": vid,
        "product_id": pid,
        "product_string": product,
        "interface_number": interface,
        "usage_page": usage_page,
        "usage": 1,
        "path": b"/dev/hidraw-test",
    }


class DeviceProfileTests(unittest.TestCase):
    def test_k61se_profile_is_verified(self) -> None:
        profile = identify(info(KEMOVE_VID, 0x010C, "KEMOVE K61SE"))
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.key, "k61se")
        self.assertTrue(profile.verified)
        self.assertEqual(profile.transport, G5KB)
        self.assertEqual(profile.keymap_len, 504)

    def test_k87_and_k61se_share_pid_but_are_distinguished_by_product(self) -> None:
        k87 = identify(info(KEMOVE_VID, 0x010C, "KEMOVE K87-1980"))
        k61 = identify(info(KEMOVE_VID, 0x010C, "KEMOVE K61SE"))
        self.assertIsNotNone(k87)
        self.assertIsNotNone(k61)
        assert k87 is not None and k61 is not None
        self.assertEqual(k87.key, "k87-1980")
        self.assertEqual(k61.key, "k61se")
        self.assertFalse(k87.verified)
        self.assertIsNone(k87.keymap_len)

    def test_k87se_uses_different_pid(self) -> None:
        profile = identify(info(KEMOVE_VID, 0x0164, "KEMOVE K87SE-1980"))
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.key, "k87se-1980")
        self.assertFalse(profile.verified)

    def test_aula_f75_receiver_uses_vendor_collection(self) -> None:
        profile = identify(
            info(AULA_VID, 0xFA09, "AULA-F75 2.4G", interface=None, usage_page=0xFF02)
        )
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.key, "aula-f75-2.4g")
        self.assertEqual(profile.transport, "cdrv3632")

    def test_wired_aula_f75_is_distinguished_from_kemove(self) -> None:
        profile = identify(info(KEMOVE_VID, 0x010C, "AULA-F75 3.0 KB"))
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.key, "aula-f75-wired")

    def test_unknown_product_on_shared_pid_is_unrecognised(self) -> None:
        self.assertIsNone(identify(info(KEMOVE_VID, 0x010C, "KEMOVE K68SE")))

    def test_every_profile_has_a_layout_file(self) -> None:
        for profile in PROFILES:
            if profile.layout is None:
                continue
            with self.subTest(profile=profile.key):
                path = profile.layout_path()
                self.assertIsNotNone(path)
                assert path is not None
                self.assertTrue(path.is_file(), f"missing layout for {profile.key}: {path}")

    def test_profile_lookup_by_key(self) -> None:
        self.assertEqual(profile_for_key("k87-1980").key, "k87-1980")  # type: ignore[union-attr]
        self.assertIsNone(profile_for_key("does-not-exist"))


if __name__ == "__main__":
    unittest.main()
