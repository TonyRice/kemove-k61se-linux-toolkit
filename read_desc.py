#!/usr/bin/env python3
import glob
import os
import re

for dev in glob.glob("/sys/class/hidraw/hidraw*"):
    uevent = os.path.join(dev, "device", "uevent")
    desc_path = os.path.join(dev, "device", "report_descriptor")

    if os.path.isfile(uevent) and os.path.isfile(desc_path):
        with open(uevent) as f:
            content = f.read()
        if "258A:010C" in content.upper():
            with open(desc_path, "rb") as f:
                desc = f.read()

            print(f"--- Found {os.path.basename(dev)} ---")
            # Parse report IDs and sizes
            i = 0
            cur_id = None
            cur_size = 0
            cur_count = 0
            while i < len(desc):
                b = desc[i]
                tag = b & 0xFC
                size = b & 0x03
                data = desc[i + 1 : i + 1 + size]

                if tag == 0x84:  # Report ID
                    cur_id = data[0] if data else 0
                elif tag == 0x74:  # Report Size (bits)
                    cur_size = data[0] if data else 0
                elif tag == 0x94:  # Report Count
                    cur_count = (
                        int.from_bytes(data, "little") if data else 0
                    )
                elif tag == 0xB0:  # Feature Report
                    total_bytes = (cur_size * cur_count) // 8
                    print(
                        f"  -> Feature Report ID 0x{cur_id:02X}: Payload = {total_bytes} bytes (Buffer with ID = {total_bytes + 1} bytes)"
                    )
                elif tag == 0x90:  # Output Report
                    total_bytes = (cur_size * cur_count) // 8
                    print(
                        f"  -> Output Report  ID 0x{cur_id:02X}: Payload = {total_bytes} bytes"
                    )

                i += 1 + size
