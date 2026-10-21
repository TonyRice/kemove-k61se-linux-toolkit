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
    packet = bytearray(521)

    # 1. hidapi Report ID
    packet[0] = 0x06

    # 2. 8-byte Header from local_26c and local_268
    packet[1] = 0x06  # Magic
    packet[2] = 0x08  # Opcode: Custom Per-Key Lighting Matrix
    packet[3] = 0x00
    packet[4] = 0x00
    packet[5] = 0x01  # Layer 1
    packet[6] = 0x00
    packet[7] = 0x7E  # Length: 126 bytes (0x007E)
    packet[8] = 0x00

    # 3. 126 Key Slots (auStack_264 starts at byte 9 in this 1-indexed packet)
    # Set all 126 keys to 0xFF (full white illumination)
    for i in range(126):
        packet[9 + i] = 0xFF

    time.sleep(0.008)
    res = handle.send_feature_report(packet)
    print(f"Sent 126-key lighting mask (Result: {res} bytes)")

finally:
    handle.close()
