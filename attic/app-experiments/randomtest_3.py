#!/usr/bin/env python3
import time
import hid

VENDOR_ID = 0x258A
PRODUCT_ID = 0x010C

target_path = None
for dev in hid.enumerate(VENDOR_ID, PRODUCT_ID):
    if dev.get("interface_number") == 1:
        target_path = dev["path"]
        break

if not target_path:
    raise SystemExit("Error: Interface 1 not detected.")

handle = hid.device()
handle.open_path(target_path)

try:
    # 521-byte Feature Report buffer (1-byte Report ID prefix + 520 payload)
    packet = bytearray(521)

    # 1. hidapi Report ID
    packet[0] = 0x06

    # 2. 8-byte Header decoded from 0x806 and 0x7e0001
    packet[1] = 0x06  # Magic
    packet[2] = 0x08  # Opcode: Lighting control
    packet[3] = 0x00
    packet[4] = 0x00
    packet[5] = 0x01  # Layer 1
    packet[6] = 0x00
    packet[7] = 0x7E  # Length: 126 bytes (0x007E)
    packet[8] = 0x00

    # 3. Payload configuration (Mode, Brightness, Speed)
    # Check the lines above local_26c in this function to see where mode is set:
    packet[9] = 0x01   # Mode 1: Static[cite: 22]
    packet[10] = 0x05  # Brightness: 5 (Max)[cite: 22]
    packet[11] = 0x03  # Speed: 3[cite: 22]

    time.sleep(0.008)
    res = handle.send_feature_report(packet)
    print(f"Sent lighting command (Result: {res} bytes)")

finally:
    handle.close()
