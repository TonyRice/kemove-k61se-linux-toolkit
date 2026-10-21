#!/usr/bin/env python3
import datetime
import fcntl
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import time

# struct usbmon_packet, 64 bytes (see Documentation/usb/usbmon.rst)
#
#   off  size  field
#    0    8   id              (u64)
#    8    1   type            ('S' submit / 'C' callback / 'E' error)
#    9    1   xfer_type       (0=ISO 1=INTR 2=CTRL 3=BULK)
#   10    1   epnum           (endpoint | 0x80 == IN)
#   11    1   devnum          (device address on the bus)
#   12    2   busnum
#   14    1   flag_setup
#   15    1   flag_data
#   16    8   ts_sec
#   24    4   ts_usec
#   28    4   status
#   32    4   length          (URB transfer_buffer_length = bytes the driver asked for)
#   36    4   len_cap         (bytes usbmon actually captured into the data area)
#   40    8   setup           (8-byte control setup packet; union w/ iso_rec)
#   48    4   interval
#   52    4   start_frame
#   56    4   xfer_flags
#   60    4   ndesc
#   --   --
#  48/64  len_cap  data       <-- payload follows; read(2) uses the 48-byte form
#
# usbmon has TWO header sizes:
#   * API0 = 48 bytes -> what read(2) delivers (mon_bin_read: hdrbytes=PKT_SZ_API0)
#   * API1 = 64 bytes -> only the MON_IOCX_GETX ioctl / mmap ring produce this
#
# read(2) copies just the first 48 bytes of the 64-byte in-kernel header and then
# appends the payload *immediately*.  So in a read() buffer the data starts at
# offset 48, NOT 64.  Assuming 64 silently ate the first 16 payload bytes and
# shortened every report by 16 (this is exactly what the old version did).
HDR_FMT_API0 = "=QcBBBHbbqiiII8s"       # 48 bytes: through setup[]
HDR_FMT_API1 = "=QcBBBHbbqiiII8siiII"   # 64 bytes: + interval/start_frame/xfer_flags/ndesc
HDR_SIZE_API0 = struct.calcsize(HDR_FMT_API0)  # 48
HDR_SIZE_API1 = struct.calcsize(HDR_FMT_API1)  # 64
SETUP_FMT = "<BBHHH"  # bmRequestType, bRequest, wValue, wIndex, wLength
READ_BUF_SIZE = 262144  # 256KB buffer prevents kernel packet truncation desync
CAPTURE_DIR = "captures"

# MON_IOCG_STATS = _IOR(0x92, 3, struct mon_bin_stats{u32 queued; u32 dropped;})
# Lets us prove whether usbmon itself dropped events (ring overflow).
MON_IOCG_STATS = 0x80089203

# MON_IOCT_RING_SIZE = _IO(0x92, 4) ; MON_IOCQ_RING_SIZE = _IO(0x92, 5)
# The default ring is only ~300 KB.  /dev/usbmon<N> carries the WHOLE bus, so a
# fast poller (keyboard/mouse INTR IN) or an app startup burst can overflow it,
# and usbmon then silently discards events — indistinguishable from real traffic
# in the output.  We enlarge it up to the kernel's BUFF_MAX (64 MiB).
MON_IOCT_RING_SIZE = 0x9204
MON_IOCQ_RING_SIZE = 0x9205
RING_SIZE_BYTES = int(os.environ.get("USB_MON_RING_SIZE", 64 * 1024 * 1024))


def fix_ownership(path):
    """Ensures created files belong to the invoking non-root user."""
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if sudo_uid and sudo_gid:
        try:
            os.chown(path, int(sudo_uid), int(sudo_gid))
        except Exception:
            pass
    try:
        if os.path.isdir(path):
            os.chmod(path, 0o777)
        else:
            os.chmod(path, 0o666)
    except Exception:
        pass


def auto_detect_k61se():
    """Finds exact live Bus and Device address of 258a:010c via lsusb."""
    try:
        out = (
            subprocess.check_output(["lsusb", "-d", "258a:010c"])
            .decode()
            .strip()
        )
        m = re.search(r"Bus (\d+) Device (\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return None, None


def decode_control_setup(setup_bytes):
    """Parses standard 8-byte USB control setup packet."""
    if len(setup_bytes) < 8:
        return ""
    bmRequestType, bRequest, wValue, wIndex, wLength = struct.unpack(
        SETUP_FMT, setup_bytes[:8]
    )
    if bmRequestType == 0 and bRequest == 0 and wValue == 0 and wIndex == 0:
        return ""
    direction = "IN" if (bmRequestType & 0x80) else "OUT"
    req_type = (bmRequestType >> 5) & 0x03

    hid_reqs = {0x01: "GET_REPORT", 0x09: "SET_REPORT"}
    r_types = {1: "Input", 2: "Output", 3: "Feature"}

    if req_type == 1:  # HID Class Request
        h_name = hid_reqs.get(bRequest, f"0x{bRequest:02X}")
        r_type_num = (wValue >> 8) & 0xFF
        r_name = r_types.get(r_type_num, f"Type_{r_type_num}")
        r_id = wValue & 0xFF
        return (
            f"[{h_name} {r_name} (ID:0x{r_id:02X}) Iface:{wIndex} Len:{wLength}]"
        )
    return (
        f"[Req:0x{bRequest:02X} {direction} wVal:0x{wValue:04X} "
        f"Iface:{wIndex} Len:{wLength}]"
    )


def pick_layout(raw, len_cap):
    """Returns the byte offset where the payload starts in a usbmon record.

    read(2) emits the API0 header only: 48 header bytes followed by the payload,
    so the record is ``48 + len_cap`` bytes and the data begins at 48.  The
    64-byte API1 header is produced only by MON_IOCX_GETX / the mmap ring; if a
    record is long enough to hold a 64-byte header we honour that instead.
    """
    if len(raw) >= HDR_SIZE_API1 + len_cap:
        return HDR_SIZE_API1
    return HDR_SIZE_API0


def format_hex_dump(data, bytes_per_line=16):
    """Formats bytes into an xxd-style hex dump."""
    if not data:
        return "<No Payload Data>"
    lines = []
    for i in range(0, len(data), bytes_per_line):
        chunk = data[i : i + bytes_per_line]
        hex_bytes = " ".join(f"{b:02X}" for b in chunk)
        ascii_repr = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:04X}  {hex_bytes:<48}  |{ascii_repr}|")
    return "\n".join(lines)


def flush_usbmon(fd):
    """Flushes stale buffer contents before a capture run."""
    while True:
        r, _, _ = select.select([fd], [], [], 0.0)
        if not r:
            break
        os.read(fd, READ_BUF_SIZE)


def get_usbmon_stats(fd):
    """Returns (queued, dropped) from usbmon's MON_IOCG_STATS ioctl.

    ``dropped`` counts events the kernel threw away since the previous call
    (ring overflow).  It must be zero for a capture to be trustworthy.
    """
    try:
        buf = bytearray(8)
        fcntl.ioctl(fd, MON_IOCG_STATS, buf)
        queued, dropped = struct.unpack("<II", buf)
        return queued, dropped
    except Exception:
        return None, None


def prepare_capture_dir(clean_tag):
    """(Re)creates captures/<tag>/ and wipes any previous capture for that tag.

    Each identifier owns exactly one folder and one capture set: re-running the
    same tag removes the old files first, so stale .bin/.raw/.md never pile up
    and a re-capture simply reuses the same folder.
    """
    tag_dir = os.path.join(CAPTURE_DIR, clean_tag)
    if os.path.isdir(tag_dir):
        shutil.rmtree(tag_dir, ignore_errors=True)
    os.makedirs(tag_dir, exist_ok=True)
    fix_ownership(tag_dir)
    return tag_dir


def get_ring_size(fd):
    """Returns usbmon's current ring buffer size in bytes (or None)."""
    try:
        return int(fcntl.ioctl(fd, MON_IOCQ_RING_SIZE))
    except Exception:
        return None


def configure_ring_size(fd, want=RING_SIZE_BYTES):
    """Enlarges the usbmon ring so a burst cannot overflow it.

    Falls back through smaller sizes for kernels with a lower BUFF_MAX.
    """
    for size in (want, 16 * 1024 * 1024, 4 * 1024 * 1024, 1024 * 1024):
        try:
            fcntl.ioctl(fd, MON_IOCT_RING_SIZE, size)
            return get_ring_size(fd)
        except OSError:
            continue
    return get_ring_size(fd)


def write_markdown_report(action_tag, description, packets, session_time, dropped=0):
    clean_tag = re.sub(r"[^a-zA-Z0-9_\-]", "_", action_tag).replace("\x00", "")
    tag_dir = os.path.join(CAPTURE_DIR, clean_tag)
    os.makedirs(tag_dir, exist_ok=True)
    md_filename = f"{clean_tag}.md"
    md_path = os.path.join(tag_dir, md_filename)

    with open(md_path, "w") as f:
        f.write(f"# Capture Report: `{clean_tag}`\n\n")
        f.write(f"- **Timestamp:** {session_time}\n")
        f.write(f"- **Packets Logged:** {len(packets)}\n")
        f.write(
            f"- **Events dropped by usbmon:** {dropped}"
            f"{' (⚠ INCOMPLETE CAPTURE)' if dropped else ' (capture complete)'}\n"
        )
        f.write(f"- **User Description / Notes:**\n  > {description}\n\n")
        f.write(
            "- **URB Len** = bytes the driver requested; **Cap** = bytes usbmon "
            "captured; **Setup** = embedded control setup flag.\n"
        )
        f.write("---\n\n")

        f.write("## Event Summary Table\n\n")
        f.write(
            "| # | Time | Stage | Dir | Type | Ep | URB Len | Cap | Setup | "
            "Status | Details / Artifact |\n"
        )
        f.write("|---|---|---|---|---|---|---|---|---|---|---|\n")

        for idx, pkt in enumerate(packets):
            bin_link = (
                f"[`{pkt['bin_name']}`]({pkt['bin_name']})"
                if pkt["bin_name"]
                else "--"
            )
            raw_link = (
                f"[`{pkt['raw_name']}`]({pkt['raw_name']})"
                if pkt.get("raw_name")
                else "--"
            )
            warn = " ⚠" if pkt["length"] != pkt["len_cap"] else ""
            f.write(
                f"| {idx + 1} | {pkt['time']} | {pkt['stage']} | {pkt['direction']} | "
                f"{pkt['xfer_name']} | {pkt['ep_addr']} | {pkt['length']} | "
                f"{pkt['len_cap']} | {pkt['flag_setup']} | "
                f"{pkt['status']} | {pkt['details']} {bin_link} {raw_link}{warn} |\n"
            )

        f.write("\n---\n\n")
        f.write("## Detailed Packet Payloads\n\n")

        for idx, pkt in enumerate(packets):
            f.write(
                f"### Event #{idx + 1}: {pkt['stage']} {pkt['direction']} "
                f"({len(pkt['data'])} bytes) - Ep {pkt['ep_addr']}\n\n"
            )
            if pkt["details"]:
                f.write(f"- **Setup:** {pkt['details']}\n")
            f.write(
                f"- **URB length:** {pkt['length']}  |  **Captured:** {pkt['len_cap']}"
                f"  |  **flags:** setup={pkt['flag_setup']} data={pkt['flag_data']}\n"
            )
            f.write(f"- **Setup hex:** `{pkt['setup_hex'] or '<empty>'}`\n")
            if pkt["length"] != pkt["len_cap"]:
                f.write(
                    f"- ⚠ **Length mismatch:** driver asked for {pkt['length']} "
                    f"bytes, usbmon captured {pkt['len_cap']}. The data below is "
                    f"the captured portion; see the `.raw` file for the whole "
                    f"usbmon record.\n"
                )
            f.write(
                f"- **Payload starts at read() offset:** {pkt['data_offset']} "
                f"(48 = API0 header, 64 = API1 header)\n"
            )
            if pkt["bin_name"]:
                f.write(f"- **Data artifact:** `{pkt['bin_name']}`\n")
            if pkt.get("raw_name"):
                f.write(
                    f"- **Raw usbmon record:** `{pkt['raw_name']}` "
                    f"(64-byte header + data)\n"
                )
            f.write("```text\n")
            f.write(format_hex_dump(pkt["data"]))
            f.write("\n```\n\n")

    fix_ownership(md_path)

    master_log = os.path.join(CAPTURE_DIR, "SESSION_INDEX.md")
    header = (
        "# K61SE Captured Actions Index\n\n"
        "| Timestamp | Action Tag | Events | Description | Report |\n"
        "|---|---|---|---|---|\n"
    )
    new_row = (
        f"| {session_time} | `{clean_tag}` | {len(packets)} | {description} | "
        f"[{clean_tag}/{clean_tag}.md]({clean_tag}/{clean_tag}.md) |\n"
    )
    # One row per identifier: keep the newest capture, drop older rows for this
    # same tag, so re-capturing reuses the slot instead of piling up.
    rows = []
    if os.path.exists(master_log):
        with open(master_log) as f:
            for line in f:
                if (
                    line.startswith("|")
                    and f"`{clean_tag}`" not in line
                    and not line.startswith("|---")
                    and not line.startswith("| Timestamp |")
                ):
                    rows.append(line)

    with open(master_log, "w") as f:
        f.write(header)
        f.writelines(rows)
        f.write(new_row)

    fix_ownership(master_log)


def record_action(fd, target_dev, action_tag):
    clean_tag = re.sub(r"[^a-zA-Z0-9_\-]", "_", action_tag).replace("\x00", "")
    tag_dir = prepare_capture_dir(clean_tag)
    input(
        f"\n[1] Press [Enter] when ready to START recording '{clean_tag}' "
        f"(folder {tag_dir}/ wiped & reused)..."
    )
    flush_usbmon(fd)
    get_usbmon_stats(fd)  # zero the kernel's "dropped" counter

    print(f"\n[>>> RECORDING STARTED for '{clean_tag}']")
    print(
        "    -> Click buttons in DeviceDriver.exe (Mouse motion is ignored)."
    )
    print("    -> Return here and press [Enter] to STOP.\n")

    packets = []
    xfer_names = {0: "ISO", 1: "INTR", 2: "CTRL", 3: "BULK"}

    while True:
        rlist, _, _ = select.select([fd, sys.stdin], [], [], 0.05)
        if sys.stdin in rlist:
            sys.stdin.readline()
            print(f"[<<< RECORDING STOPPED: {len(packets)} event(s) logged]")
            break

        if fd in rlist:
            raw = os.read(fd, READ_BUF_SIZE)
            if len(raw) < HDR_SIZE_API0:
                continue

            (
                pkt_id,
                raw_pkt_type,
                xfer_type,
                epnum,
                dev,
                b_num,
                flag_setup,
                flag_data,
                ts_sec,
                ts_usec,
                status,
                length,
                len_cap,
                setup,
            ) = struct.unpack(HDR_FMT_API0, raw[:HDR_SIZE_API0])

            # read(2) copied a 48-byte header, so the payload begins at offset
            # 48 (not 64).  Slicing at 64 discarded the first 16 payload bytes.
            data_off = pick_layout(raw, len_cap)
            data = raw[data_off : data_off + len_cap]

            # 1. HARD FILTER: Reject any device that is not the K61SE
            if dev != target_dev:
                continue

            # 2. HARD FILTER: Drop ISO (0) and Bulk (3) video/audio noise
            if xfer_type not in (1, 2):
                continue

            direction = "IN" if (epnum & 0x80) else "OUT"
            ep_addr = epnum & 0x0F

            # 3. HARD FILTER: Drop routine keystroke & mouse movement polling (INTR IN)
            if xfer_type == 1 and direction == "IN":
                continue

            # 4. Drop empty control callbacks with no setup or data
            if xfer_type == 2 and len(data) == 0 and setup == b"\x00" * 8:
                continue

            pkt_time = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

            # Safe string mapping to avoid null-byte filename errors
            if raw_pkt_type == b"S":
                stage = "SUBMIT"
            elif raw_pkt_type == b"C":
                stage = "CALLBACK"
            elif raw_pkt_type == b"E":
                stage = "ERROR"
            else:
                stage = "EVENT"

            xfer_str = xfer_names.get(xfer_type, str(xfer_type))
            details = decode_control_setup(setup) if xfer_type == 2 else ""

            evt_idx = len(packets) + 1
            bin_name = None
            raw_name = f"{clean_tag}_evt{evt_idx}_{stage.lower()}_{direction.lower()}_{len_cap}B.raw"
            raw_path = os.path.join(tag_dir, raw_name)
            with open(raw_path, "wb") as rf:
                rf.write(raw)
            fix_ownership(raw_path)

            if len(data) > 0:
                bin_name = (
                    f"{clean_tag}_evt{evt_idx}_{stage.lower()}_{direction.lower()}"
                    f"_{len(data)}B.bin"
                )
                bin_path = os.path.join(tag_dir, bin_name)
                with open(bin_path, "wb") as bf:
                    bf.write(data)
                fix_ownership(bin_path)

            packets.append(
                {
                    "time": pkt_time,
                    "stage": stage,
                    "direction": direction,
                    "ep_addr": ep_addr,
                    "xfer_name": xfer_str,
                    "status": status,
                    "length": length,
                    "len_cap": len_cap,
                    "flag_setup": flag_setup,
                    "flag_data": flag_data,
                    "setup": setup,
                    "setup_hex": " ".join(f"{b:02X}" for b in setup),
                    "data_offset": data_off,
                    "details": details,
                    "data": data,
                    "bin_name": bin_name,
                    "raw_name": raw_name,
                }
            )

            hdr_preview = (
                " ".join(f"{b:02X}" for b in data[:24])
                if data
                else "<no payload>"
            )
            warn = ""
            if length != len_cap:
                warn = f"  ⚠ URB len {length} != captured {len_cap}"
            if len(data) != len_cap:
                warn += f"  ⚠ parsed {len(data)} of {len_cap} bytes"
            print(
                f"    [{len(packets):>2}] {stage:<8} {direction:<3} {xfer_str:<4} "
                f"Ep:{ep_addr} | URB:{length:<4} Cap:{len_cap:<4} Status:{status} "
                f"| {details}{warn}"
            )
            if data:
                print(
                    f"         Payload: {hdr_preview}{'...' if len(data) > 24 else ''}"
                )

    _, dropped = get_usbmon_stats(fd)
    if dropped:
        print(
            f"[!] WARNING: usbmon dropped {dropped} event(s) (ring overflow) — "
            f"this capture is incomplete, re-run it."
        )
    else:
        print("[*] usbmon reported 0 dropped events (capture is complete).")

    return packets, dropped


def main():
    if os.geteuid() != 0:
        print(f"Error: Must run as root -> sudo python3 {sys.argv[0]}")
        sys.exit(1)

    os.system("modprobe usbmon 2>/dev/null")
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    fix_ownership(CAPTURE_DIR)

    bus, devnum = auto_detect_k61se()
    if not bus:
        print("[-] Error: K61SE (258a:010c) not found in lsusb.")
        print("    -> Ensure the USB cable is firmly connected.")
        sys.exit(1)

    dev_node = f"/dev/usbmon{bus}"
    print(f"[*] Auto-detected K61SE (258a:010c): Bus {bus}, Device Address {devnum}")
    print(f"[*] Monitoring exclusive node: {dev_node}")
    print(
        "[*] Video/audio streams (ISO/Bulk) and mouse polling (INTR IN) are filtered out."
    )
    print(
        "[*] Every event also writes a full .raw usbmon record (header+data), "
        "so no bytes are lost to parsing."
    )
    print(
        "[*] read(2) uses the 48-byte API0 header, so payloads are sliced at "
        "offset 48 (the old 64 ate the first 16 bytes)."
    )

    fd = os.open(dev_node, os.O_RDONLY)
    ring = configure_ring_size(fd)
    print(
        f"[*] usbmon ring buffer enlarged to: {ring} bytes "
        f"(default was ~300 KB — too small for a startup burst)"
    )

    try:
        while True:
            # Re-check device address before each action in case of hardware re-enumeration
            cur_bus, cur_dev = auto_detect_k61se()
            if cur_dev and cur_dev != devnum:
                print(
                    f"[!] Device re-enumerated: Address updated from {devnum} to {cur_dev}"
                )
                devnum = cur_dev
                if cur_bus != bus:
                    os.close(fd)
                    bus = cur_bus
                    dev_node = f"/dev/usbmon{bus}"
                    fd = os.open(dev_node, os.O_RDONLY)
                    print(f"[!] Re-bound to new bus node: {dev_node}")

            print("\n" + "=" * 60)
            tag = input(
                "Enter action tag (e.g. 'off', 'on', 'static_white') [or 'q' to quit]: "
            ).strip()
            if not tag or tag.lower() == "q":
                break

            tag = re.sub(r"[^a-zA-Z0-9_\-]", "_", tag).lower()
            packets, dropped = record_action(fd, devnum, tag)

            if not packets:
                print("[!] No packets captured. Skipping documentation.")
                continue

            session_time = datetime.datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            desc = input(
                "\nDescribe what you clicked & visual result:\n> "
            ).strip()
            if not desc:
                desc = "No visual notes recorded."

            write_markdown_report(tag, desc, packets, session_time, dropped)
            print(f"[+] Saved report: {CAPTURE_DIR}/{tag}/{tag}.md")
            print(f"[+] Updated index:  {CAPTURE_DIR}/SESSION_INDEX.md")

    finally:
        os.close(fd)
        print("\n[*] Sniffer session ended.")


if __name__ == "__main__":
    main()
