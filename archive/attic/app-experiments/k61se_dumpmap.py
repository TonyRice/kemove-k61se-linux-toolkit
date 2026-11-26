#!/usr/bin/env python3
import hid

VENDOR_ID = 0x258A
PRODUCT_ID = 0x010C  # Confirmed K61SE PID

# Target the confirmed management interface (Interface 1)
target_path = None
for dev in hid.enumerate(VENDOR_ID, PRODUCT_ID):
    if dev.get("interface_number") == 1:
        target_path = dev["path"]
        break

if not target_path:
    raise SystemExit("Error: Interface 1 not detected.")

print(f"Opening Interface 1 at path: {target_path.decode(errors='ignore')}")
handle = hid.device()
handle.open_path(target_path)

try:
    print("Issuing GetFeature request for Report ID 0x06 (521 bytes)...")
    raw_report = handle.get_feature_report(0x06, 521)

    print(f"Read successful: Received {len(raw_report)} bytes from MCU.")

    # Save stock state locally
    with open("k61se_stock_dump.bin", "wb") as f:
        f.write(bytes(raw_report))

    hex_header = " ".join(f"{b:02X}" for b in raw_report[:16])
    print(f"Header Preview: {hex_header}")

    # Inspect first key entry (Slot 0, ESC) at offset 9
    if len(raw_report) >= 13:
        slot0_bytes = " ".join(f"{b:02X}" for b in raw_report[9:13])
        print(f"Slot 0 (ESC) Bytes: {slot0_bytes}")

except Exception as err:
    print(f"HID Transaction Failed: {err}")
finally:
    handle.close()
