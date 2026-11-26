#!/usr/bin/env python3
import sys


def compare_bins(file_a, file_b):
    with open(file_a, "rb") as f:
        data_a = f.read()
    with open(file_b, "rb") as f:
        data_b = f.read()

    print(f"Comparing:")
    print(f"  File A: {file_a} ({len(data_a)} bytes)")
    print(f"  File B: {file_b} ({len(data_b)} bytes)")
    print("-" * 65)
    print(f"{'Offset':<8} | {'Hex Offset':<12} | {'File A (Hex)':<14} | {'File B (Hex)':<14}")
    print("-" * 65)

    diffs = 0
    max_len = max(len(data_a), len(data_b))
    for i in range(max_len):
        b_a = f"{data_a[i]:02X}" if i < len(data_a) else "--"
        b_b = f"{data_b[i]:02X}" if i < len(data_b) else "--"

        if b_a != b_b:
            print(f"{i:<8} | 0x{i:04X}       | 0x{b_a:<12} | 0x{b_b:<12}")
            diffs += 1

    print("-" * 65)
    print(f"Total differing bytes: {diffs}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: python3 {sys.argv[0]} <file_a.bin> <file_b.bin>")
        sys.exit(1)
    compare_bins(sys.argv[1], sys.argv[2])
