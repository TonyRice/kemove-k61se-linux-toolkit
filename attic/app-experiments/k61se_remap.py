#!/usr/bin/env python3
import time
import xml.etree.ElementTree as ET
import hid

VENDOR_ID = 0x258A
PRODUCT_ID = 0x010C

def build_matrix_from_xml(xml_file="K61SE-keyboard.xml"):
    """Populates the 126-slot (504-byte) matrix from the stock XML layout."""
    tree = ET.parse(xml_file)
    root = tree.getroot()

    # 126 slots * 4 bytes = 504 bytes
    matrix = bytearray(504)

    for key in root.findall(".//key"):
        idx = int(key.get("key_index"))
        code = int(key.get("code"), 16)
        offset = idx * 4

        if offset + 3 < 504:
            if code == 0xAF:
                # Internal Fn marker from FUN_0040e130
                matrix[offset + 0] = 0x00
                matrix[offset + 1] = 0x0D
                matrix[offset + 2] = 0x00
                matrix[offset + 3] = 0x00
            else:
                matrix[offset + 0] = 0x00
                matrix[offset + 1] = 0x00
                matrix[offset + 2] = code & 0xFF
                matrix[offset + 3] = 0x00

    # Slot 0 fallback from FUN_0040e130 (Esc = 0x29)
    matrix[0] = 0x00
    matrix[1] = 0x00
    matrix[2] = 0x29
    matrix[3] = 0x00

    return matrix

def remap_slot(matrix, key_index, new_hid_code):
    """Rebinds a specific key_index to a standard USB HID code."""
    offset = key_index * 4
    matrix[offset + 0] = 0x00
    matrix[offset + 1] = 0x00
    matrix[offset + 2] = new_hid_code & 0xFF
    matrix[offset + 3] = 0x00

def build_packet(matrix):
    """Builds the verified 520-byte feature report."""
    pkt = bytearray(520)
    pkt[0] = 0x06  # Report ID
    pkt[1] = 0x03  # Opcode: Write Keymap
    pkt[2] = 0x00  # Sub-command
    pkt[3] = 0x01  # Profile 1
    pkt[4] = 0xF8  # Length Low: 504 bytes (0x01F8)
    pkt[5] = 0x01  # Length High
    pkt[6] = 0x00
    pkt[7] = 0x00

    # Copy 504-byte matrix into packet starting at offset 8
    pkt[8:512] = matrix[:504]
    return pkt

def main():
    target_path = None
    for dev in hid.enumerate(VENDOR_ID, PRODUCT_ID):
        if dev.get("interface_number") == 1:
            target_path = dev["path"]
            break

    if not target_path:
        raise SystemExit("Error: K61SE Interface 1 not detected.")

    print("Building default matrix from K61SE-keyboard.xml...")
    matrix = build_matrix_from_xml("K61SE-keyboard.xml")

    # Example: Hardware remapping for permanent arrow keys
    # /? (key_index 64) -> Up Arrow (0x52)
    # Menu (key_index 59) -> Left Arrow (0x50)
    # Right Alt (key_index 53) -> Down Arrow (0x51)
    # Right Ctrl (key_index 65) -> Right Arrow (0x4F)
    #
    # Test with Menu key first (key_index 59 -> Left Arrow 0x50):
    print("Remapping Menu key (key_index 59) -> Left Arrow (0x50)...")
    remap_slot(matrix, key_index=59, new_hid_code=0x50)

    packet = build_packet(matrix)

    handle = hid.device()
    handle.open_path(target_path)
    try:
        time.sleep(0.008)  # 8ms timing delay from FUN_00444750
        res = handle.send_feature_report(packet)
        print(f"Keymap written to MCU ({res} bytes transferred).")

        # Optional handshake query matching FUN_00444750
        time.sleep(0.005)
        ack = handle.get_feature_report(0x06, 8)
        print("MCU ACK: " + " ".join(f"{b:02X}" for b in ack))
        print("\nSuccess! Tap the Menu key to verify Left Arrow scancode output.")
    finally:
        handle.close()

if __name__ == "__main__":
    main()
