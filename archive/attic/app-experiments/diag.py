#!/usr/bin/env python3
import sys
import hid

VENDOR_ID = 0x258A

# Find any matching SinoWealth devices
devices = hid.enumerate(VENDOR_ID)

if not devices:
    print(
        f"No devices found with Vendor ID 0x{VENDOR_ID:04X}. Check 'lsusb | grep 258a'."
    )
    sys.exit(1)

print(f"Found {len(devices)} HID node(s) for VID 0x{VENDOR_ID:04X}:")
for idx, d in enumerate(devices):
    print(
        f" [{idx}] PID: 0x{d['product_id']:04X} | Interface: {d['interface_number']} | Path: {d['path'].decode(errors='ignore')}"
    )

# Select Interface 1, or fall back to the second enumerated node if interface numbers are -1
target_path = None
for d in devices:
    if d.get("interface_number") == 1:
        target_path = d["path"]
        break

if not target_path and len(devices) > 1:
    print("\nWarning: Interface 1 not explicitly numbered. Selecting node [1]...")
    target_path = devices[1]["path"]

if not target_path:
    print("Could not isolate the second HID node.")
    sys.exit(1)

dev = hid.device()
try:
    dev.open_path(target_path)
    print("\nConnected to target interface. Sending GetFeature(0x06)...")

    # Request 521 bytes (Report ID 0x06 + 520 bytes buffer)
    data = dev.get_feature_report(0x06, 521)

    print(f"Success! Read {len(data)} bytes.")
    with open("k61se_stock_dump.bin", "wb") as f:
        f.write(bytes(data))

    hex_header = " ".join(f"{b:02X}" for b in data[:16])
    print(f"Header preview: {hex_header}")

except Exception as e:
    print(f"HID Transaction Failed: {e}")
finally:
    dev.close()
