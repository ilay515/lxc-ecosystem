#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_DIR="$DIR/images"
DISK="$IMAGES_DIR/pve-disk.qcow2"
ISO="$IMAGES_DIR/proxmox-ve.iso"
QMP_SOCK="$IMAGES_DIR/qmp.sock"

# Clean old socket if any
rm -f "$QMP_SOCK"

# RAM and Cores for the Proxmox VM
RAM_MB=8192
SMP_CORES=4

# Check if disk exists
if [ ! -f "$DISK" ]; then
    echo "Creating 64G virtual disk at $DISK..."
    qemu-img create -f qcow2 "$DISK" 64G
fi

# Determine display mode: headless with VNC by default, or GTK if --gui is passed
DISPLAY_ARGS=(-display none -vnc 127.0.0.1:0)
if [[ "${1:-}" == "--gui" ]]; then
    DISPLAY_ARGS=(-display gtk -vnc 127.0.0.1:0)
fi

# Determine boot arguments
BOOT_ARGS=()
if [ -f "$ISO" ]; then
    # Prioritize hard disk 'c', fallback to cdrom 'd'
    BOOT_ARGS+=(-cdrom "$ISO" -boot order=c,menu=on)
else
    BOOT_ARGS+=(-boot c)
fi

echo "=========================================================="
echo " Starting Proxmox VE VM"
echo " Web UI / API: https://localhost:8006"
echo " SSH:          ssh -p 2222 root@localhost"
echo " VNC Console:  vnc://127.0.0.1:5900"
echo "=========================================================="

exec sg kvm -c "qemu-system-x86_64 \
    -name proxmox-ve \
    -enable-kvm \
    -cpu host \
    -smp $SMP_CORES \
    -m $RAM_MB \
    -drive file=\"$DISK\",if=virtio,format=qcow2 \
    ${BOOT_ARGS[*]} \
    -netdev user,id=net0,hostfwd=tcp::8006-:8006,hostfwd=tcp::2222-:22 \
    -device virtio-net-pci,netdev=net0 \
    -qmp unix:\"$QMP_SOCK\",server,nowait \
    ${DISPLAY_ARGS[*]} \
    -vga std"
