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

def send_prompt_ack(opcode, payload_len=0x80):
    pkt = bytearray(520)
    pkt[0] = 0x06
    pkt[1] = opcode & 0xFF
    pkt[2] = 0x01
    pkt[3] = 0x00
    pkt[4] = 0x01
    pkt[5] = 0x00
    pkt[6] = payload_len & 0xFF
    pkt[7] = (payload_len >> 8) & 0xFF

    time.sleep(0.008)
    handle.send_feature_report(pkt)
    time.sleep(0.005)
    return handle.get_feature_report(0x06, 8)

def set_keyboard_color(color_id=7, mode_id=1):
    """
    color_id:
      1 = Red, 2 = Green, 3 = Blue, 4 = Yellow,
      5 = Purple, 6 = Cyan, 7 = White, 8 = Rainbow
    mode_id:
      1 = Static, 2 = Breathing
    """
    colors = {
        1: "Red", 2: "Green", 3: "Blue", 4: "Yellow",
        5: "Purple", 6: "Cyan", 7: "White", 8: "Rainbow"
    }
    print(f"Setting Color to {colors.get(color_id, 'Custom')} (ID: {color_id}) | Mode: {mode_id}")

    # Step 1: Probe Settings
    send_prompt_ack(0x84, payload_len=0x80)
    time.sleep(0.08)

    # Step 2: Settings Register
    pkt_settings = bytearray(520)
    pkt_settings[0] = 0x06
    pkt_settings[1] = 0x04
    pkt_settings[2] = 0x01
    pkt_settings[3] = 0x00
    pkt_settings[4] = 0x01
    pkt_settings[5] = 0x00
    pkt_settings[6] = 0x80
    pkt_settings[7] = 0x00
    pkt_settings[17] = 0x01                          # Backlight ON
    pkt_settings[19] = (mode_id + 0x20) & 0xFF       # Mode offset

    time.sleep(0.008)
    handle.send_feature_report(pkt_settings)
    time.sleep(0.08)

    # Step 3: Probe Matrix
    send_prompt_ack(0x86, payload_len=0x7E)
    time.sleep(0.08)

    # Step 4: Write Trigger with Target Color ID at Byte 2
    pkt_mask = bytearray(520)
    pkt_mask[0] = 0x06
    pkt_mask[1] = 0x06
    pkt_mask[2] = color_id & 0xFF                    # Preset Color ID
    pkt_mask[3] = 0x00
    pkt_mask[4] = 0x01
    pkt_mask[5] = 0x00
    pkt_mask[6] = 0x7E
    pkt_mask[7] = 0x00

    # 126 key illumination mask
    for i in range(126):
        pkt_mask[8 + i] = 0xFF

    time.sleep(0.008)
    res = handle.send_feature_report(pkt_mask)
    time.sleep(0.08)
    print(f"Applied color change ({res} bytes).")

try:
    target_color = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    target_mode = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    set_keyboard_color(color_id=target_color, mode_id=target_mode)
finally:
    handle.close()
