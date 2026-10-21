#!/usr/bin/env python3
import datetime
import json
import os
import struct
import sys
import time

BUS = 3
DEV_NODE = f"/dev/usbmon{BUS}"
CAPTURE_DIR = "captures"

# struct usbmon_packet layout (64 bytes in Linux kernel)
HDR_FMT = "=QcBBBHbbqiiII8siiII"
HDR_SIZE = 64


def ensure_env():
    if os.geteuid() != 0:
        print(f"Error: Must run as root -> sudo python3 {sys.argv[0]}")
        sys.exit(1)
    if not os.path.exists(DEV_NODE):
        print(f"Error: {DEV_NODE} not found. Run: sudo modprobe usbmon")
        sys.exit(1)
    os.makedirs(CAPTURE_DIR, exist_ok=True)


def capture_single_action(mon, action_label):
    print(f"\n[*] Armed for action: '{action_label}'")
    print("[*] Click 'Apply' / 'Save' in DeviceDriver.exe now...")

    captured_packets = []
    start_time = time.time()

    # Listen until packets arrive or 10-second timeout
    while time.time() - start_time < 10.0:
        raw_hdr = mon.read(HDR_SIZE)
        if len(raw_hdr) < HDR_SIZE:
            break

        (
            pkt_id,
            pkt_type,
            xfer_type,
            epnum,
            devnum,
            busnum,
            flag_setup,
            flag_data,
            ts_sec,
            ts_usec,
            status,
            length,
            len_cap,
            setup,
            interval,
            start_frame,
            xfer_flags,
            ndesc,
        ) = struct.unpack(HDR_FMT, raw_hdr)

        payload = mon.read(len_cap) if len_cap > 0 else b""

        # Filter: Host-to-Device Submit ('S'), non-empty payload >= 8 bytes
        # Excludes periodic USB polling reads and zero-length control ACKs
        if pkt_type == b"S" and len(payload) >= 8:
            timestamp = datetime.datetime.now().isoformat()
            captured_packets.append(
                {
                    "timestamp": timestamp,
                    "devnum": devnum,
                    "epnum": epnum,
                    "xfer_type": xfer_type,
                    "length": len(payload),
                    "hex": payload.hex().upper(),
                    "raw_bytes": payload,
                }
            )

            # Once packets start flowing from a single click, capture all frames
            # across a 250ms window to catch multi-packet handshake pairs
            window_start = time.time()
            while time.time() - window_start < 0.25:
                hdr = mon.read(HDR_SIZE)
                if len(hdr) < HDR_SIZE:
                    break
                p_id, p_type, _, ep, dev, _, _, _, _, _, _, _, l_cap, _, _, _, _, _ = (
                    struct.unpack(HDR_FMT, hdr)
                )
                data = mon.read(l_cap) if l_cap > 0 else b""
                if p_type == b"S" and len(data) >= 8:
                    captured_packets.append(
                        {
                            "timestamp": datetime.datetime.now().isoformat(),
                            "devnum": dev,
                            "epnum": ep,
                            "xfer_type": xfer_type,
                            "length": len(data),
                            "hex": data.hex().upper(),
                            "raw_bytes": data,
                        }
                    )
            break

    return captured_packets


def record_session():
    ensure_env()
    mon = open(DEV_NODE, "rb")

    session_log_path = os.path.join(CAPTURE_DIR, "session_log.json")
    if os.path.exists(session_log_path):
        with open(session_log_path, "r") as f:
            session_history = json.load(f)
    else:
        session_history = []

    print("==================================================")
    print("       K61SE Controlled USB Capture Harness       ")
    print("==================================================")
    print(f"Artifacts will be stored under: ./{CAPTURE_DIR}/\n")

    try:
        while True:
            action = input(
                "Enter test label (e.g. 'static_red', 'static_white', 'off') [or 'q' to quit]: "
            ).strip()
            if not action or action.lower() == "q":
                break

            packets = capture_single_action(mon, action)

            if not packets:
                print(
                    "[!] No outgoing packets detected within timeout. Try again."
                )
                continue

            print(
                f"[+] Captured {len(packets)} outgoing packet(s) for '{action}':"
            )

            for idx, p in enumerate(packets):
                bin_filename = f"{action}_pkt{idx}_{int(time.time())}.bin"
                bin_path = os.path.join(CAPTURE_DIR, bin_filename)

                with open(bin_path, "wb") as f:
                    f.write(p["raw_bytes"])

                entry = {
                    "label": action,
                    "packet_index": idx,
                    "timestamp": p["timestamp"],
                    "length": p["length"],
                    "bin_file": bin_filename,
                    "hex_preview": " ".join(
                        f"{b:02X}" for b in p["raw_bytes"][:32]
                    ),
                    "hex_full": p["hex"],
                }
                session_history.append(entry)

                print(f"    [{idx}] {p['length']} bytes -> saved to {bin_path}")
                print(f"        Header: {entry['hex_preview']}")

            with open(session_log_path, "w") as f:
                json.dump(session_history, f, indent=2)

            print(f"[+] Updated {session_log_path}\n")

    except KeyboardInterrupt:
        print("\n[*] Exiting.")
    finally:
        mon.close()


if __name__ == "__main__":
    record_session()
