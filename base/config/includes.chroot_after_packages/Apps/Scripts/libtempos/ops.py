import sys
import os
import re
import time
import uuid
import hashlib
import json
import shutil
from libtempos.utils import run_cmd

def perform_power_action(action):
    print("Closing applications...")
    for attempt in range(60):
        out = run_cmd("wmctrl -l")
        windows_to_close = []
        lines = out.splitlines()
        for line in lines:
            if not re.search(r'(Desktop$|Bureaublad$|xfce4-panel$)', line):
                parts = line.split()
                if parts:
                    windows_to_close.append(parts[0]) 

        if attempt == 0:
            for win_id in windows_to_close:
                run_cmd(f"wmctrl -ic {win_id}")

        if not windows_to_close:
            break
        
        print(f"Waiting for windows to close: {len(windows_to_close)} remaining...")
        time.sleep(1)

    print("Syncing disks...")
    run_cmd("sync")
    
    if action == "shutdown":
        print("Shutting down...")
        run_cmd("xfce4-session-logout --halt --fast")
    elif action == "reboot":
        print("Rebooting...")
        run_cmd("xfce4-session-logout --reboot --fast")
    sys.exit(0)

def MountTempOsBootMedium(storage, unmount=False):
    """
    Mounts or unmounts the TempOS boot medium.
    Returns (success_bool, message).
    """
    if not storage.boot_is_tempos:
        return False, "Boot medium is not identified as TempOS."
    if not storage.boot_mount_device:
        return False, "Boot medium partition mount device not found."

    if unmount:
        if not os.path.ismount(storage.mount_point):
            return True, "Target /media/user/TempOS is not mounted."
        
        run_cmd(f"umount {storage.mount_point}", as_root=True)
        run_cmd(f"rmdir {storage.mount_point}", as_root=True)
        run_cmd("sync")
        return True, "Unmounted successfully."
    else:
        if os.path.ismount(storage.mount_point):
             return True, "Already mounted at target."

        run_cmd(f"mkdir -p {storage.mount_point}", as_root=True)
        run_cmd(f"chown 1000:1000 {storage.mount_point}", as_root=True)
        cmd = f"mount {storage.boot_mount_device} {storage.mount_point} -o rw,uid=1000,gid=1000"
        res = run_cmd(cmd, as_root=True)
        run_cmd(f"chown 1000:1000 {storage.mount_point}", as_root=True)
        
        if os.path.ismount(storage.mount_point):
            return True, "Mounted successfully."
        else:
            return False, f"Mount command failed. Output: {res}"

def add_wifi_adaptors(settings):
    cnt = 0
    nm_path = "/etc/NetworkManager/system-connections"
    for i, item in enumerate(settings.wifi):
        if not item or len(item) < 1: continue
        ssid = item[0].strip() if item[0] else ""
        pwd = item[1].strip() if len(item) > 1 and item[1] else ""
        if not ssid: continue
        
        u = uuid.uuid4()
        prio = (i + 1) * 10
        
        lines = []
        lines.append(f"[connection]\nid={ssid}\nuuid={u}\ntype=wifi\nautoconnect=true\nautoconnect-priority={prio}\n\n")
        lines.append(f"[wifi]\nmode=infrastructure\nssid={ssid}\n\n")
        if pwd: lines.append(f"[wifi-security]\nkey-mgmt=wpa-psk\npsk={pwd}\n\n")
        lines.append(f"[ipv4]\nmethod=auto\n\n[ipv6]\naddr-gen-mode=stable-privacy\nmethod=auto\n\n[proxy]\n")
        
        content = "".join(lines)
        tmp = f"/tmp/{ssid}.nmconnection"
        dst = f"{nm_path}/{ssid}.nmconnection"
        
        try:
            with open(tmp, 'w') as f: f.write(content)
            run_cmd(f"mv '{tmp}' '{dst}'", as_root=True)
            run_cmd(f"chown root:root '{dst}'", as_root=True)
            run_cmd(f"chmod 600 '{dst}'", as_root=True)
            cnt += 1
        except Exception: pass
        
    if cnt > 0:
        run_cmd("systemctl restart NetworkManager", as_root=True)
        return f"Added {cnt} WiFi connections."

    return "No WiFi connections defined."

def verify_image_file(storage, img_path, allow_missing=True, clear_cache_first=True):
    sha_path = img_path + ".sha256"
    
    if not os.path.exists(img_path): return allow_missing, f"Image missing: {img_path}"
    if not os.path.exists(sha_path): return allow_missing, f"SHA256 missing: {sha_path}"
    
    try:
        if clear_cache_first:
            clear_cache()
            
        with open(sha_path, 'r') as f: expected = f.read(64).lower()
        
        sha = hashlib.sha256()
        with open(img_path, 'rb') as f:
            while True:
                chunk = f.read(1048576) # 1MB chunks
                if not chunk: break
                sha.update(chunk)
        actual = sha.hexdigest().lower()
        
        if actual == expected: return True, "Image verification successful."
        return False, f"Image contains errors! Please reinstall."
    except Exception as e: return False, f"Verify error: {e}"

def verify_booted_image(storage, img_path, allow_missing=True):
    sha_path = img_path + ".sha256"
    
    if not os.path.exists(sha_path): return allow_missing, f"SHA256 missing: {sha_path}"
    
    device_path = storage.ventoy_iso_device
    if not os.path.exists(device_path): return allow_missing, f"Boot device missing: {device_path}"
    
    try:
        with open(sha_path, 'r') as f: expected = f.read(64).lower()
        
        # Accessing block device requires root, use system tool via sudo
        out = run_cmd(["sha256sum", "-b", device_path], as_root=True)
        if not out:
            return False, "Failed to calculate checksum (permission denied or device busy?)"
        
        # Output format is "hash  filename"
        actual = out.split()[0].lower()
        
        if actual == expected: return True, "Boot image verification successful."
        return False, f"Boot image ({device_path}) contains errors! Please reinstall. Actual: {actual}, Expected: {expected}"
    except Exception as e: return False, f"Verify error: {e}"

def clear_cache():
    run_cmd("sh -c 'echo 3 > /proc/sys/vm/drop_caches'", as_root=True)

def set_ventoy_default(storage, iso_filename):
    """Updates ventoy.json to set the specified ISO as default."""
    v_json = storage.ventoy_filename
    if os.path.exists(v_json):
        iso_rel_path = f"/{storage.TempOs_Brand_Name}/{iso_filename}"
        try:
            with open(v_json, 'r') as f: c = f.read()
            if "VTOY_DEFAULT_IMAGE" in c:
                c = re.sub(r'("VTOY_DEFAULT_IMAGE":\s*)(.*?)(\s*\})', f'\\1"{iso_rel_path}"\\3', c, flags=re.DOTALL)
                with open(v_json, 'w') as f: f.write(c)
                return True
        except Exception: pass
    return False

def create_bookmarks(storage, settings):
    """Creates/Overwrites the user's Chromium Bookmarks file with favorites from settings."""
    if not settings.favorites:
        return

    config_dir = os.path.join(storage.home, ".config/chromium/Default")
    bookmarks_file = os.path.join(config_dir, "Bookmarks")

    if not os.path.exists(config_dir):
        os.makedirs(config_dir, exist_ok=True)

    children = []
    current_id = 100
    
    for item in settings.favorites:
        if not item or len(item) < 2: continue
        name, url = item[0].strip(), item[1].strip()
        if not name or not url: continue
        children.append({
           "date_added": "13200000000000000",
           "id": str(current_id),
           "name": name,
           "type": "url",
           "url": url
        })
        current_id += 1

    # JSON Structure
    data = {
       "version": 1,
       "checksum": "",
       "roots": {
          "bookmark_bar": {
             "children": children,
             "name": "Bookmarks Bar",
             "type": "folder"
          },
          "other": {
             "children": [],
             "name": "Other Bookmarks",
             "type": "folder"
          },
          "synced": {
             "children": [],
             "name": "Mobile Bookmarks",
             "type": "folder"
          }
       }
    }

    with open(bookmarks_file, 'w') as f:
        json.dump(data, f, indent=3)

def sync_root_items(src_dir, dst_dir, move=False):
    """
    Synchronizes root-level items (files and folders) from src_dir into dst_dir:
    - Iterates over items in src_dir
    - If item exists in dst_dir, deletes it (directory with contents or file)
    - Copies (or moves) item from src_dir to dst_dir
    - Items existing in dst_dir but not in src_dir are preserved
    """
    if not os.path.exists(src_dir):
        return

    if not os.path.exists(dst_dir):
        try:
            os.makedirs(dst_dir, exist_ok=True)
        except Exception:
            run_cmd(f"mkdir -p '{dst_dir}'", as_root=True)

    try:
        items = os.listdir(src_dir)
    except Exception:
        return

    for item in items:
        s_item = os.path.join(src_dir, item)
        d_item = os.path.join(dst_dir, item)

        if os.path.lexists(d_item):
            try:
                if os.path.isdir(d_item) and not os.path.islink(d_item):
                    shutil.rmtree(d_item)
                else:
                    os.unlink(d_item)
            except Exception:
                run_cmd(f"rm -rf '{d_item}'", as_root=True)

        try:
            if move:
                shutil.move(s_item, d_item)
            else:
                if os.path.isdir(s_item) and not os.path.islink(s_item):
                    shutil.copytree(s_item, d_item, symlinks=True)
                else:
                    shutil.copy2(s_item, d_item, follow_symlinks=False)
        except Exception:
            if move:
                run_cmd(f"mv '{s_item}' '{d_item}'", as_root=True)
            else:
                if os.path.isdir(s_item):
                    run_cmd(f"cp -a '{s_item}' '{d_item}'", as_root=True)
                else:
                    run_cmd(f"cp -p '{s_item}' '{d_item}'", as_root=True)