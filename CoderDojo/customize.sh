#!/bin/bash

# Arguments passed from build.sh
TARGET_BUILD_DIR="$1"
BRANCH_NAME="$2"

# Get the directory where THIS script is located (e.g., ./CoderDojo)
SCRIPT_DIR="$(dirname "$0")"

echo "    -> [Customizer] Applying changes for $BRANCH_NAME..."

# ---------------------------------------------------------
# Task 1: Copy/Overwrite config files
# ---------------------------------------------------------
if [ -d "$SCRIPT_DIR/config" ]; then
    echo "    -> Copying config overlays..."
    # cp -a preserves attributes and overwrites destination files
    cp -a "$SCRIPT_DIR/config/"* "$TARGET_BUILD_DIR/config/"
fi

# ---------------------------------------------------------
# Task 2: Modify files
# ---------------------------------------------------------
#GRUB_FILE="$TARGET_BUILD_DIR/config/bootloaders/grub-pc/grub.cfg"
#
#if [ -f "$GRUB_FILE" ]; then
#    echo "    -> Updating Hostname in GRUB config..."
#    sed -i "s/{HOSTNAME}/$BRANCH_NAME/g" "$GRUB_FILE"
#else
#    echo "    -> Warning: GRUB config not found at $GRUB_FILE, skipping replacement."
#fi

# ---------------------------------------------------------
# Task 3: Deelte files
# ---------------------------------------------------------
# rm "$TARGET_BUILD_DIR/config/includes.chroot/usr/share/images/logo.png"

echo "    -> [Customizer] Finished."