#!/bin/bash

# Get the absolute path of the directory containing this script, no matter where the user executes it from.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
VM_IMAGE="$PROJECT_ROOT/vm-build/vm-build.qcow2"

if [ ! -f "$VM_IMAGE" ]; then
    echo "Error: VM disk not found at $VM_IMAGE. Follow instructions from vm-build/setup.txt"
    exit 1
fi

echo "Booting VM... Project root ($PROJECT_ROOT) mapped as 'hostshare'"
echo "Follow ./vm-build/setup.txt the first time!"
echo "Shutdown the VM by entering poweroff"

qemu-system-x86_64 \
  -enable-kvm \
  -m 4096 \
  -smp 4 \
  -nographic \
  -drive file="$VM_IMAGE",format=qcow2,if=virtio \
  -fsdev local,security_model=mapped-xattr,id=fsdev0,path="$PROJECT_ROOT" \
  -device virtio-9p-pci,id=fs0,fsdev=fsdev0,mount_tag=hostshare