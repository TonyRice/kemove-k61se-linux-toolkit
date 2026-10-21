#!/usr/bin/env python3
import time
import xml.etree.ElementTree as ET
import hid

VENDOR_ID = 0x258A
# Common SinoWealth K61SE PIDs seen in DeviceDriver.exe strings:
# 0x0164, 0x0155, 0x010c
PRODUCT_ID = 0x0164


def build_default_matrix(xml_path="K61SE-keyboard.xml"):
    """Parses K61SE-keyboard.xml and populates the 126-slot (504-byte) matrix."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # 126 keys * 4 bytes per key = 504 bytes
    matrix = bytearray(504)

    # Populate defaults from XML
    for key in root.findall(".//key"):
        # key_index in XML maps directly to the internal slot
        idx = int(key.get("key_index"))
        code = int(key.get("code"), 16)

        slot_offset = idx * 4
        if slot_offset + 3 < 504:
            if code == 0xAF:
                # Internal Fn Key marker verified from Ghidra
                matrix[slot_offset + 0] = 0x00
                matrix[slot_offset + 1] = 0x0D
                matrix[slot_offset + 2] = 0x00
                matrix[slot_offset + 3] = 0x00
            else:
                # Standard USB HID key
                matrix[slot_offset + 0] = 0x00
                matrix[slot_offset + 1] = 0x00
                matrix[slot_offset + 2] = code
                matrix[slot_offset + 3] = 0x00

    # Ensure Slot 0 (ESC) is set per FUN_0040e130 fallback
    matrix[0] = 0x00
    matrix[1] = 0x00
    matrix[2] = 0x29
    matrix[3] = 0x00

    return matrix


def remap_key(matrix, key_index, hid_code):
    """Overwrites a specific key slot with a standard USB HID scancode."""
    offset = key_index * 4
    if offset + 3 >= 504:
        raise ValueError(f"Key index {key_index} out of bounds.")
    matrix[offset + 0] = 0x00
    matrix[offset + 1] = 0x00
    matrix[offset + 2] = hid_code
    matrix[offset + 3] = 0x00


def build_feature_report(matrix, profile=1):
    """Constructs the 520-byte vendor packet with proper HIDAPI report prefix."""
    # Byte 0: Report ID for hidapi (0x06)
    # Bytes 1-8: Header matching FUN_0040e130
    # Bytes 9-512: 504-byte Matrix
    # Bytes 513-520: Zero padding
    packet = bytearray(521)

    packet[0] = 0x06  # HID Feature Report ID
    packet[1] = 0x06  # Magic
    packet[2] = 0x03  # Command: Write Keymap
    packet[3] = 0x00  # Sub-command
    packet[4] = profile & 0xFF  # Layer/Profile (1)
    packet[5] = 0xF8  # Data Length Low: 504 (0x01F8)
    packet[6] = 0x01  # Data Length High
    packet[7] = 0x00  # Reserved
    packet[8] = 0x00  # Reserved

    # Copy 504-byte matrix payload
    packet[9:513] = matrix[0:504]

    return packet


def find_device():
    for dev in hid.enumerate(VENDOR_ID):
        # MI_01 is the vendor management interface
        if dev.get("interface_number") == 1:
            return dev["path"], dev["product_id"]
    return None, None


def main():
    dev_path, pid = find_device()
    if not dev_path:
        raise SystemExit(
            "Kemove K61SE Management Interface (MI_01) not found. "
            "Ensure device is connected and udev rules allow raw hidaccess."
        )

    print(f"Connecting to K61SE (VID: 0x{VENDOR_ID:04X}, PID: 0x{pid:04X})...")
    matrix = build_default_matrix("K61SE-keyboard.xml")

    # Example: Remap Menu key (key_index=59) to Left Arrow (0x50)
    # Menu default code: 0x65 -> Left Arrow: 0x50
    remap_key(matrix, key_index=59, hid_code=0x50)

    report = build_feature_report(matrix, profile=1)

    dev = hid.device()
    dev.open_path(dev_path)

    try:
        # Match high-precision timing from binary: 8ms sleep
        time.sleep(0.008)

        # Transmit 521-byte prefixed feature report
        res = dev.send_feature_report(report)
        if res < 0:
            raise RuntimeError(f"Failed to send feature report (code: {res})")

        print("Firmware keymap successfully updated.")
    finally:
        dev.close()


if __name__ == "__main__":
    main()
