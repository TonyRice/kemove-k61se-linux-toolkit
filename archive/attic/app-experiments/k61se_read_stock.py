#!/usr/bin/env python3
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

def read_hardware_sector(read_opcode, payload_len=504, profile=1):
    # Total buffer: 520 bytes (0x208 matching FUN_004448b0)
    prompt_pkt = bytearray(520)
    
    # Corrected alignment: Byte 0 is Report ID, Byte 1 is Opcode
    prompt_pkt[0] = 0x06                        # Report ID
    prompt_pkt[1] = read_opcode & 0xFF          # Opcode (0x83 or 0x84)
    prompt_pkt[2] = 0x00                        # Sub-command
    prompt_pkt[3] = profile & 0xFF              # Profile / Layer (1)
    prompt_pkt[4] = payload_len & 0xFF          # Length Low (0xF8 = 504)
    prompt_pkt[5] = (payload_len >> 8) & 0xFF   # Length High (0x01)
    prompt_pkt[6] = 0x00
    prompt_pkt[7] = 0x00

    time.sleep(0.008)  # 8ms setup delay
    
    # 1. Send the read prompt
    res = handle.send_feature_report(prompt_pkt)
    if res < 0:
        raise RuntimeError(f"Prompt 0x{read_opcode:02X} failed: {res}")
    
    # 2. Allow MCU memory controller to stage EEPROM to USB RAM
    time.sleep(0.005)

    # 3. Fetch the staged 520-byte payload
    data = handle.get_feature_report(0x06, 520)
    return bytes(data)

try:
    print("--- 1. Querying Live Keymap Matrix (Opcode 0x83) ---")
    keymap_dump = read_hardware_sector(read_opcode=0x83, payload_len=504)
    print(f"Total bytes received: {len(keymap_dump)}")
    print("Header: " + " ".join(f"{b:02X}" for b in keymap_dump[:8]))
    
    if len(keymap_dump) >= 16:
        # Key matrix slots start at byte offset 8
        print("Slot 0 (Esc): " + " ".join(f"{b:02X}" for b in keymap_dump[8:12]))
        print("Slot 1 (Tab): " + " ".join(f"{b:02X}" for b in keymap_dump[12:16]))

    with open("k61se_stock_keymap.bin", "wb") as f:
        f.write(keymap_dump)

    time.sleep(0.035)

    print("\n--- 2. Querying Settings Block (Opcode 0x84) ---")
    settings_dump = read_hardware_sector(read_opcode=0x84, payload_len=126)
    print(f"Total bytes received: {len(settings_dump)}")
    print("Header: " + " ".join(f"{b:02X}" for b in settings_dump[:8]))

    with open("k61se_settings_dump.bin", "wb") as f:
        f.write(settings_dump)

    print("\nCapture complete: Saved 'k61se_stock_keymap.bin' and 'k61se_settings_dump.bin'.")

finally:
    handle.close()
