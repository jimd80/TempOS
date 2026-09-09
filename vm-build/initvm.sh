#!/bin/bash
# initvm.sh

# If running locally on host, print the instructions.
if ! grep -iq "QEMU" /sys/class/dmi/id/sys_vendor 2>/dev/null; then
    echo "=========================================================="
    echo " VM BOOTSTRAP INSTRUCTIONS                                "
    echo "=========================================================="
    echo "You are currently on the host machine. To setup the VM,   "
    cat setup.txt
    echo "=========================================================="
    exit 0
fi

# RUNNING INSIDE THE VIRTUAL MACHINE
if [ "$EUID" -ne 0 ]; then
    echo "Error: Please run this script as root inside the VM."
    exit 1
fi

# Set hostname
hostnamectl set-hostname vm-build
sed -i '/127.0.1.1/d' /etc/hosts
echo -e "127.0.1.1\tvm-build" >> /etc/hosts

# Auto mount /mnt/project
if ! grep -q "hostshare" /etc/fstab; then
    echo "Configuring /etc/fstab for automatic mounting..."
    echo "hostshare /mnt/project 9p trans=virtio,version=9p2000.L,rw,_netdev 0 0" >> /etc/fstab
fi

# Ensuring folder is mounted...
mkdir -p /mnt/project
mount -a

# Auto login
echo "Configuring automatic root login..."
GETTY_DIR="/etc/systemd/system/serial-getty@ttyS0.service.d"
mkdir -p "$GETTY_DIR"
cat <<EOF > "$GETTY_DIR/autologin.conf"
[Service]
ExecStart=
ExecStart=-/sbin/agetty -o '-p -f \\u' --keep-baud 115200,38400,9600 --noclear --autologin root %I \$TERM
EOF

systemctl daemon-reload

# Set cwd to /mnt/project
if ! grep -q "cd /mnt/project" /root/.bashrc; then
    echo "Setting default directory to /mnt/project"
    echo -e "\n# Auto-jump to mapped folder on login\ncd /mnt/project" >> /root/.bashrc
fi

# Set grub to 0 seconds
echo "Disable grub delay..."
sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=0/' /etc/default/grub
sed -i 's/^GRUB_TIMEOUT_STYLE=.*/GRUB_TIMEOUT_STYLE=hidden/' /etc/default/grub
mkdir -p /etc/default/grub.d
cat <<EOF > /etc/default/grub.d/99-pico-fastboot.cfg
GRUB_TIMEOUT=0
GRUB_TIMEOUT_STYLE=hidden
GRUB_RECORDFAIL_TIMEOUT=0
EOF
update-grub

# Init apt
echo "Init apt..."
sed -i.bak 's/^Components: .*/Components: main contrib non-free non-free-firmware/' /etc/apt/sources.list.d/debian.sources
apt update

# Apply image size increase
apt install -y cloud-guest-utils
growpart /dev/vda 1
resize2fs /dev/vda1

# Install software
echo "Installing live-build and dependancies..."
apt update
apt install -y net-tools mc
apt install -y live-build live-config grub-efi
apt install -y firmware-b43-installer firmware-b43legacy-installer

#cp -r /lib/firmware/b43 /mnt/project/base/config/includes.chroot/lib/firmware/
#cp -r /lib/firmware/b43legacy /mnt/project/base/config/includes.chroot_after_packages/lib/firmware/

echo "=========================================================="
echo " SUCCESS! The VM is initialized."
echo " Your project on the host is mapped to /mnt/project on the VM"
echo "=========================================================="