#!/usr/bin/env python3
import sys
import time
import hid

VENDOR_ID = 0x258A
PRODUCT_ID = 0x010C

def find_mgmt_interface():
    for dev in hid.enumerate(VENDOR_ID, PRODUCT_ID):
        if dev.get("interface_number") == 1:
            return dev["path"]
    return None

dev_path = find_mgmt_interface()
if not dev_path:
    raise SystemExit("Error: K61SE Interface 1 not detected.")

handle = hid.device()
handle.open_path(dev_path)

def set_led_state(power=True, mode_index=0):
    """
    power: True = LED Switch ON, False = LED Switch OFF (Byte 17)
    mode_index: 0 = Static, 1 = Breathing, 2 = Wave, etc.
    """
    state_str = "ON" if power else "OFF"
    print(f"Setting Backlight Switch: {state_str} | Mode: {mode_index}")

    # --- Step 1: Probe Settings (Opcode 0x84) ---
    pkt_probe_settings = bytearray(520)
    pkt_probe_settings[0] = 0x06
    pkt_probe_settings[1] = 0x84
    pkt_probe_settings[2] = 0x01
    pkt_probe_settings[3] = 0x00
    pkt_probe_settings[4] = 0x01
    pkt_probe_settings[5] = 0x00
    pkt_probe_settings[6] = 0x80  # 128 bytes length at bytes 6-7
    pkt_probe_settings[7] = 0x00

    time.sleep(0.008)
    handle.send_feature_report(pkt_probe_settings)
    time.sleep(0.005)
    
    # Read existing 128-byte block to preserve base registers
    settings_block = bytearray(handle.get_feature_report(0x06, 520))
    time.sleep(0.08)  # Sleep(0x50) = 80ms

    # --- Step 2: Write Settings (Opcode 0x04) ---
    # Apply header
    settings_block[0] = 0x06
    settings_block[1] = 0x04
    settings_block[2] = 0x01
    settings_block[3] = 0x00
    settings_block[4] = 0x01
    settings_block[5] = 0x00
    settings_block[6] = 0x80
    settings_block[7] = 0x00

    # Apply LED Switch (Byte 17) and Mode (Byte 19)
    settings_block[17] = 0x01 if power else 0x00
    settings_block[19] = (mode_index + 0x20) & 0xFF

    time.sleep(0.008)
    handle.send_feature_report(settings_block)
    time.sleep(0.08)  # Sleep(0x50) = 80ms

    # --- Step 3: Probe Matrix (Opcode 0x86) ---
    pkt_probe_matrix = bytearray(520)
    pkt_probe_matrix[0] = 0x06
    pkt_probe_matrix[1] = 0x86
    pkt_probe_matrix[2] = 0x01
    pkt_probe_matrix[3] = 0x00
    pkt_probe_matrix[4] = 0x01
    pkt_probe_matrix[5] = 0x00
    pkt_probe_matrix[6] = 0x7E  # 126 keys length at bytes 6-7
    pkt_probe_matrix[7] = 0x00

    time.sleep(0.008)
    handle.send_feature_report(pkt_probe_matrix)
    time.sleep(0.08)  # Sleep(0x50) = 80ms

    # --- Step 4: Write Trigger & Key Mask (Opcode 0x06) ---
    pkt_mask = bytearray(520)
    pkt_mask[0] = 0x06
    pkt_mask[1] = 0x06
    pkt_mask[2] = mode_index & 0xFF
    pkt_mask[3] = 0x00
    pkt_mask[4] = 0x01
    pkt_mask[5] = 0x00
    pkt_mask[6] = 0x7E
    pkt_mask[7] = 0x00

    # Fill 126 key mask bytes (0xFF = illuminated, 0x00 = off)
    mask_val = 0xFF if power else 0x00
    for i in range(126):
        pkt_mask[8 + i] = mask_val

    time.sleep(0.008)
    res = handle.send_feature_report(pkt_mask)
    time.sleep(0.08)
    print(f"Committed: {res} bytes transferred.")

try:
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "on"
    mode = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    if cmd == "off":
        set_led_state(power=False, mode_index=0)
    else:
        set_led_state(power=True, mode_index=mode)
finally:
    handle.close()
