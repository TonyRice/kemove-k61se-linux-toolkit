#!/usr/bin/env python3
import sys
import time
import hid

VENDOR_ID = 0x258A
PRODUCT_ID = 0x010C

# 1. Locate MI_01
target_path = None
for dev in hid.enumerate(VENDOR_ID, PRODUCT_ID):
    if dev.get("interface_number") == 1:
        target_path = dev["path"]
        break

if not target_path:
    raise SystemExit("Error: K61SE Interface 1 not detected.")

handle = hid.device()
handle.open_path(target_path)

def set_color(r, g, b):
    print(f"Reading live settings from K61SE...")

    # Probe Opcode 0x04 (Settings, 128 bytes)
    probe = bytearray(520)
    probe[0] = 0x06
    probe[1] = 0x04
    probe[4] = 0x01
    probe[6] = 0x80  # 128 bytes
    
    time.sleep(0.02)
    handle.send_feature_report(probe)
    time.sleep(0.03)
    raw = handle.get_feature_report(0x06, 520)
    
    # Extract the live 128-byte payload
    payload = bytearray(raw[8:8 + 128])
    if len(payload) < 128:
        raise RuntimeError("Failed to read settings payload from board.")

    print("Current Header: " + " ".join(f"{b:02X}" for b in payload[:16]))

    # Set Mode 1 (Static) at Byte 10
    payload[10] = 0x01

    # Overwrite the active RGB color at Bytes 11, 12, 13
    payload[11] = r & 0xFF
    payload[12] = g & 0xFF
    payload[13] = b & 0xFF

    # Repack into 520-byte Feature Report with verified framing
    write_pkt = bytearray(520)
    write_pkt[0] = 0x06  # Report ID
    write_pkt[1] = 0x04  # Opcode 0x04 (Write Settings)
    write_pkt[4] = 0x01  # Profile 1
    write_pkt[6] = 0x80  # 128 bytes length
    write_pkt[8:8 + 128] = payload

    time.sleep(0.02)
    res = handle.send_feature_report(write_pkt)
    print(f"Wrote {res} bytes to K61SE. Mode 1 set with RGB({r}, {g}, {b}).")

try:
    # Usage: python3 k61se_direct_color.py [R] [G] [B]
    # Default is pure solid white (255, 255, 255)
    red = int(sys.argv[1]) if len(sys.argv) > 1 else 255
    green = int(sys.argv[2]) if len(sys.argv) > 2 else 255
    blue = int(sys.argv[3]) if len(sys.argv) > 3 else 255
    set_color(red, green, blue)
finally:
    handle.close()
