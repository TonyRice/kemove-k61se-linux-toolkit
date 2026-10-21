#!/bin/sh
# Install the K61SE input/hidraw uaccess rule and reload udev.
# Replug the keyboard once afterward so the new ACLs are applied.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RULE="$ROOT/packaging/70-kemove-input.rules"

if [ ! -f "$RULE" ]; then
    echo "missing rule: $RULE" >&2
    exit 1
fi

echo "[*] installing $RULE"
sudo install -Dm644 "$RULE" /etc/udev/rules.d/70-kemove-input.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=input --action=change
sudo udevadm trigger --subsystem-match=hidraw --action=change
sudo udevadm settle

echo "[+] installed /etc/udev/rules.d/70-kemove-input.rules"
echo "    replug the K61SE, then check:"
echo '    getfacl "$(readlink -f /dev/input/by-id/usb-BY_Tech_KEMOVE_K61-event-kbd)"'
