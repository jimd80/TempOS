#!/bin/bash
# Usage: sudo ./build.sh <branchname>
# Example: sudo ./build.sh CoderDojo

if ! grep -iq "QEMU" /sys/class/dmi/id/sys_vendor 2>/dev/null; then
    echo "Error: Please run this script inside the VM."
    exit 0
fi

# RUNNING INSIDE THE VIRTUAL MACHINE
if [ "$EUID" -ne 0 ]; then
    echo "Error: Please run this script as root inside the VM."
    exit 1
fi

BRANCH=$1
BASE_DIR="./base"
# We use absolute path for BUILD_DIR so we can pass it safely to sub-scripts
BUILD_DIR="$(pwd)/build/$BRANCH"
SOURCE_DIR="./$BRANCH"

set -e
chmod +x */customize.sh 2>/dev/null || true

# Validation
if [ -z "$BRANCH" ]; then
    echo "Error: Please specify a branch name (e.g., ./build.sh CoderDojo)"
    exit 1
fi

if [ ! -d "$SOURCE_DIR" ]; then
    echo "Error: Branch directory '$SOURCE_DIR' does not exist."
    exit 1
fi

echo "--- Preparing Build for Branch: $BRANCH ---"

# Prepare Build Directory (Preserve Cache)
echo "[*] Clean build directory: $BUILD_DIR"
mkdir -p "$BUILD_DIR"

# Delete everything in build dir EXCEPT the './cache' folder
find "$BUILD_DIR" -mindepth 1 -maxdepth 1 ! -name 'cache' -exec rm -rf {} +

# Copy Base Image
echo "[*] Copying Base configuration..."
cp -r "$BASE_DIR/"* "$BUILD_DIR/"

# Branding (grub menu itemnames)
cp "$BUILD_DIR/config/bootloaders/grub-pc/config-keepstick.cfg" "$BUILD_DIR/config/bootloaders/grub-pc/config.cfg"
cp "$BUILD_DIR/config/bootloaders/isolinux/config-keepstick.cfg" "$BUILD_DIR/config/bootloaders/isolinux/config.cfg"
sed -i "s|\[TempOS\]|$BRANCH|g" "$BUILD_DIR/config/bootloaders/isolinux/config.cfg"
sed -i "s|\[TempOS\]|$BRANCH|g" "$BUILD_DIR/config/bootloaders/grub-pc/grub.cfg"

# Run the Branch Customization Script
CUSTOMIZE_SCRIPT="$SOURCE_DIR/customize.sh"

if [ -f "$CUSTOMIZE_SCRIPT" ]; then
    echo "[*] Running customization script..."
    chmod +x "$CUSTOMIZE_SCRIPT"
    
    # We execute the script and pass the BUILD_DIR and BRANCH name as arguments
    # We execute it from the ROOT path, as requested.
    "$CUSTOMIZE_SCRIPT" "$BUILD_DIR" "$BRANCH"
else
    echo "Warning: No customize.sh found in $SOURCE_DIR"
fi

# set scripts executable and unpack app zip files
chmod +x "$BUILD_DIR/auto/"*
chmod 0755 -R "$BUILD_DIR/config/hooks"* || true
chmod go+r -R "$BUILD_DIR/config/includes.chroot_after_packages/"* || true
chmod 0755 "$BUILD_DIR/config/includes.chroot_after_packages/Apps/Scripts/"* || true
chmod 0755 "$BUILD_DIR/config/includes.chroot_after_packages/lib/live/config/"* || true
chmod 0755 "$BUILD_DIR/config/includes.chroot_after_packages/etc/skel/Bureaublad/"*.desktop || true
chmod 0600 "$BUILD_DIR/config/includes.chroot_after_packages/etc/NetworkManager/system-connections/"* || true
chmod 0755 "$BUILD_DIR/config/includes.chroot_after_packages/usr/local/bin/"* || true
#find ./config/includes.chroot_after_packages/Apps/ -path "*.AppImage" -print0|xargs -0 chmod 0755

#unpack zipped apps (which includes the correct execute permissions)
APPS_DIR="$BUILD_DIR/config/includes.chroot_after_packages/Apps"
for archive in "$APPS_DIR"/*.tar.gz "$APPS_DIR"/*.zip; do
    [ -e "$archive" ] || continue
    echo "Extracting: $archive"
    case "$archive" in
        *.tar.gz) tar -xf "$archive" -C "$APPS_DIR" ;;
        *.zip) unzip -q -o "$archive" -d "$APPS_DIR" ;;
    esac
    rm "$archive"
done

# Rename ventoy-* to Ventoy
for v_dir in "$APPS_DIR"/ventoy-*/; do
    if [ -d "$v_dir" ]; then
        echo "Renaming $v_dir to $APPS_DIR/Ventoy"
        mv "$v_dir" "$APPS_DIR/Ventoy"
        break
    fi
done

# 5. Versioning and Info File
echo "[*] Generating Build Info..."

VERSION_FILE="$SOURCE_DIR/version"
if [ -f "$VERSION_FILE" ]; then
    VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')
else
    echo "Error: '$VERSION_FILE' not found."
    exit 1
fi

TARGET_ISO="${BRANCH}-${VERSION}.iso"
GENERATED_ISO="${BRANCH}-${VERSION}-amd64.hybrid.iso"
BUILD_DATE=$(date '+%Y/%m/%d %H:%M:%S')
INFO_DIR="$BUILD_DIR/config/includes.chroot_after_packages/etc/skel/.config/TempOS"
INFO_FILE="$INFO_DIR/info"
mkdir -p "$INFO_DIR"
cat > "$INFO_FILE" <<EOF
BrandName=$BRANCH
Version=$VERSION
Image=$TARGET_ISO
BuildDate=$BUILD_DATE
EOF
cat "$INFO_FILE"
echo

# 6. Run Live-Build Commands
echo "[*] Live-Build clean..."
cd "$BUILD_DIR"
lb clean

echo "[*] Live-Build config..."
lb config --iso-volume "${BRANCH}-${VERSION}" --image-name "${BRANCH}-${VERSION}"

echo "[*] Live-Build..."
lb build

HASH_FILE="${TARGET_ISO}.sha256"

if [ ! -f "$GENERATED_ISO" ]; then
    echo "Error: '$GENERATED_ISO' not found. The live-build process failed."
    exit 1
fi

mv "$GENERATED_ISO" "$TARGET_ISO"

echo "[*] Calc iso hash..."
sha256sum "$TARGET_ISO" | awk '{print $1}' > "$HASH_FILE"

echo "--- Build Done ---"
echo "ISO:  $BUILD_DIR/$TARGET_ISO"
echo "Hash: $BUILD_DIR/$HASH_FILE"


#GENERATED_ISO=$(find . -maxdepth 1 -type f -name "*.iso" | head -n 1)
#if [ -f "$GENERATED_ISO" ]; then

#    echo "[*] Renaming '$GENERATED_ISO' to '$TARGET_ISO'..."
#    mv "$GENERATED_ISO" "$TARGET_ISO"
#    sha256sum "$TARGET_ISO" | awk '{print $1}' > "$HASH_FILE"

#    echo "--- Build Done ---"
#    echo "ISO:  $BUILD_DIR/$TARGET_ISO"
#    echo "Hash: $BUILD_DIR/$HASH_FILE"
#else
#    echo "Error: No ISO file found. The live-build process may have failed."
#    exit 1
#fi