#!/usr/bin/env python3
import time
import xml.etree.ElementTree as ET
import hid

VENDOR_ID = 0x258A
PRODUCT_ID = 0x010C


class K61SE:

    def __init__(self):
        target_path = None
        for dev in hid.enumerate(VENDOR_ID, PRODUCT_ID):
            if dev.get("interface_number") == 1:
                target_path = dev["path"]
                break
        if not target_path:
            raise RuntimeError("K61SE Interface 1 (MI_01) not found.")

        self.dev = hid.device()
        self.dev.open_path(target_path)

    def close(self):
        self.dev.close()

    def _send_report(self, opcode, payload, subcmd=0, length=None):
        size = len(payload) if length is None else length
        pkt = bytearray(520)
        pkt[0] = 0x06  # Report ID
        pkt[1] = opcode & 0xFF  # Opcode
        pkt[2] = subcmd & 0xFF  # Sub-command
        pkt[3] = 0x00  # Reserved
        pkt[4] = 0x01  # Profile 1
        pkt[5] = 0x00  # Reserved
        pkt[6] = size & 0xFF  # Size Low Byte
        pkt[7] = (size >> 8) & 0xFF  # Size High Byte

        if payload:
            pkt[8 : 8 + len(payload)] = payload

        time.sleep(0.008)  # 8ms hardware timing delay
        return self.dev.send_feature_report(pkt)

    def _read_report(self, opcode, size):
        # Issue probe request
        self._send_report(opcode | 0x80, bytearray(0), length=size)
        time.sleep(0.02)
        raw = self.dev.get_feature_report(0x06, 520)
        return bytearray(raw[8 : 8 + size])

    # --- 1. KEY REMAPPING ENGINE (Opcode 0x03) ---
    def flash_keymap(self, remaps=None, xml_file="K61SE-keyboard.xml"):
        """Populates baseline matrix from XML and writes 504-byte payload."""
        tree = ET.parse(xml_file)
        root = tree.getroot()

        matrix = bytearray(504)
        for key in root.findall(".//key"):
            idx = int(key.get("key_index"))
            code = int(key.get("code"), 16)
            offset = idx * 4
            if offset + 3 < 504:
                if code == 0xAF:  # Internal Fn key marker
                    matrix[offset + 1] = 0x0D
                else:
                    matrix[offset + 2] = code & 0xFF

        matrix[2] = 0x29  # Slot 0 hardcoded fallback (Esc)

        # Apply custom hardware rebindings
        if remaps:
            for idx, hid_code in remaps.items():
                offset = idx * 4
                matrix[offset + 0] = 0x00
                matrix[offset + 1] = 0x00
                matrix[offset + 2] = hid_code & 0xFF
                matrix[offset + 3] = 0x00

        # Probe and Flash Keymap
        self._send_report(0x83, bytearray(0), length=504)
        time.sleep(0.08)
        self._send_report(0x03, matrix, length=504)
        time.sleep(0.08)

    # --- 2. HARDWARE TOGGLES (Opcode 0x04) ---
    def set_toggles(self, fn_lock=False, win_lock=False, mac_mode=False):
        """Controls hardware feature locks at Byte 23."""
        settings = self._read_report(0x04, 128)
        time.sleep(0.08)

        mask = 0
        if fn_lock:
            mask |= 0x01
        if win_lock:
            mask |= 0x02
        if mac_mode:
            mask |= 0x04

        settings[23] = mask
        self._send_report(0x04, settings, length=128)
        time.sleep(0.08)

    # --- 3. LIGHTING ENGINE (Opcode 0x04 + Opcode 0x06) ---
    def set_lighting(self, mode_index=0, power=True):
        """Coordinates two-stage lighting transaction from FUN_0040faa0."""
        # Stage 1: Write Settings Block
        settings = self._read_report(0x04, 128)
        time.sleep(0.08)

        settings[17] = 0x01 if power else 0x00
        settings[19] = (mode_index + 0x20) & 0xFF

        self._send_report(0x04, settings, length=128)
        time.sleep(0.08)

        # Stage 2: Trigger Mode & Key Mask
        mask = bytearray(126)  # Keep 0x00 for built-in animation playback
        self._send_report(0x86, bytearray(0), length=126)
        time.sleep(0.08)
        self._send_report(0x06, mask, subcmd=mode_index, length=126)
        time.sleep(0.08)


if __name__ == "__main__":
    kb = K61SE()
    try:
        # Example 1: Clear any stuck Fn-Lock/Arrow-Lock
        kb.set_toggles(fn_lock=False, win_lock=False, mac_mode=False)

        # Example 2: Permanent hardware arrow remapping on the bottom-right cluster
        # /? (64) -> Up (0x52), RAlt (53) -> Left (0x50), Menu (59) -> Down (0x51), RCtrl (65) -> Right (0x4F)
        arrow_cluster = {64: 0x52, 53: 0x50, 59: 0x51, 65: 0x4F}
        kb.flash_keymap(remaps=arrow_cluster)

        # Example 3: Set built-in lighting mode (0 = Static, 1 = Breathing, 2 = Wave)
        kb.set_lighting(mode_index=1, power=True)
    finally:
        kb.close()
