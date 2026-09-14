import os
import re
import shutil
import time
import urllib.request
import glob
from libtempos.utils import run_cmd, to_mb
from libtempos.ops import verify_image_file, set_ventoy_default, sync_root_items

def check_online_update(url):
    """
    Downloads update info from URL.
    Expected format lines: {name} {isohash} {isourl} {ziphash} {zipurl}
    Returns list of dicts.
    """
    updates = []
    content = run_cmd(f"curl -s -f -L '{url}'")
    
    if not content:
        return []

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        parts = line.split()
        
        if len(parts) >= 3:
            updates.append({
                'type': 'online',
                'name': parts[0],
                'iso_hash': parts[1],
                'iso_url': parts[2],
                'zip_hash': parts[3] if len(parts) > 3 else "",
                'zip_url': parts[4] if len(parts) > 4 else ""
            })
    return updates

def check_partition_update(storage, partition_device):
    """Mounts partition and lists ISOs in /{brandname}/"""
    updates = []
    mount_point = None

    part_info = next((p for p in storage.partitions if p['PartitionDevice'] == partition_device), None)
    
    if part_info and part_info['IsMounted'] and part_info['MountPoint']:
        mount_point = part_info['MountPoint']
    else:
        mount_point = storage.mount_point_update
        if not os.path.exists(mount_point):
            run_cmd(f"mkdir -p '{mount_point}'", as_root=True)
            run_cmd(f"chown 1000:1000 '{mount_point}'", as_root=True)
        
        if os.path.ismount(mount_point):
            src = run_cmd(f"findmnt -n -o SOURCE {mount_point}").strip()
            if src != partition_device:
                run_cmd(f"umount {mount_point}", as_root=True)
                run_cmd(f"mount {partition_device} {mount_point} -o ro,uid=1000,gid=1000", as_root=True)
        else:
            run_cmd(f"mount {partition_device} {mount_point} -o ro,uid=1000,gid=1000", as_root=True)

        if not os.path.ismount(mount_point):
            return []

    brand_dir = f"{mount_point}/{storage.TempOs_Brand_Name}"
    if os.path.exists(brand_dir):
        files = [f for f in os.listdir(brand_dir) if f.lower().endswith(".iso")]
        for f in files:
            name_no_ext = os.path.splitext(f)[0]
            updates.append({
                'type': 'local',
                'name': name_no_ext,
                'filename': f,
                'path': os.path.join(brand_dir, f),
                'mount_point': mount_point
            })
            
    return updates

def _download_with_progress(url, dest, msg_prefix):
    """Generator to download file with progress reporting."""
    req = urllib.request.Request(url, headers={'User-Agent': 'TempOsTool/1.0'})
    with urllib.request.urlopen(req) as response:
        total_size = int(response.info().get('Content-Length', 0))
        chunk_size = 1024 * 256
        bytes_done = 0
        last_yield = 0
        
        with open(dest, 'wb') as f:
            while True:
                chunk = response.read(chunk_size)
                if not chunk: break
                f.write(chunk)
                bytes_done += len(chunk)
                
                now = time.time()
                if now - last_yield >= 1.0:
                    mb_done = bytes_done // (1024 * 1024)
                    if total_size > 0:
                        pct = (bytes_done * 100 // total_size)
                        mb_total = total_size // (1024 * 1024)
                        yield f"{msg_prefix}: {mb_done} MB of {mb_total} MB ({pct} %)", True
                    else:
                        yield f"{msg_prefix}: {mb_done} MB downloaded", True
                    last_yield = now

def run_update_sequence(storage, options):
    item = options['item']
    dest_dir = storage.images_location
    if not os.path.exists(dest_dir):
        yield "ERROR", f"Destination directory missing: {dest_dir}", False
        return

    # Check free space on mount_point (requires >= 2 * current ISO size)
    curr_iso_size = 0
    if storage.tempos_image:
        curr_iso_path = os.path.join(dest_dir, storage.tempos_image)
        if os.path.exists(curr_iso_path):
            curr_iso_size = os.path.getsize(curr_iso_path)

    if curr_iso_size == 0 and storage.images:
        for img in storage.images:
            if img.get('isCurrent') and os.path.exists(img['path']):
                curr_iso_size = os.path.getsize(img['path'])
                break
        if curr_iso_size == 0 and os.path.exists(storage.images[0]['path']):
            curr_iso_size = os.path.getsize(storage.images[0]['path'])

    if curr_iso_size > 0:
        mp = storage.mount_point if os.path.ismount(storage.mount_point) else dest_dir
        try:
            st = os.statvfs(mp)
            free_bytes = st.f_bavail * st.f_frsize
            required_bytes = curr_iso_size * 2
            if free_bytes < required_bytes:
                yield "ERROR", f"Not enough free space on {storage.mount_point}. Required: {to_mb(required_bytes)} MB (2x ISO size), Available: {to_mb(free_bytes)} MB.", False
                return
        except Exception as e:
            print(f"Error checking free space: {e}")

    if item['type'] == 'local':
        src_path = item['path']
        src_dir = os.path.dirname(src_path)
        mount_point = item.get('mount_point')
        fname = item['filename']
        dest_path = os.path.join(dest_dir, fname)

        yield "INFO", f"Starting local update from {src_path}...", False

        if options.get('verify_src'):
            yield "INFO", "Verifying source image...", False
            ok, msg = verify_image_file(storage, src_path)
            if not ok:
                yield "ERROR", f"Source verification failed: {msg}", False
                return

        yield "INFO", f"Copying {fname}...", False
        try:
            shutil.copy2(src_path, dest_path)
            src_sha = src_path + ".sha256"
            if os.path.exists(src_sha): shutil.copy2(src_sha, dest_path + ".sha256")
        except Exception as e:
            yield "ERROR", f"Copy failed: {e}", False
            return

        if options.get('verify_dst'):
            yield "INFO", "Verifying installed image...", False
            ok, msg = verify_image_file(storage, dest_path)
            if not ok:
                yield "ERROR", f"Destination verification failed: {msg}", False
                return

        if options.get('use_settings'):
            yield "INFO", "Updating settings (TempOS.ini)...", False
            src_ini = os.path.join(src_dir, "TempOS.ini")
            if os.path.exists(src_ini): shutil.copy2(src_ini, os.path.join(dest_dir, "TempOS.ini"))

        if options.get('update_apps', True):
            src_apps = os.path.join(src_dir, "AppsExt")
            dest_apps = os.path.join(dest_dir, "AppsExt")
            if os.path.exists(src_apps):
                yield "INFO", "Syncing AppsExt...", False
                sync_root_items(src_apps, dest_apps, move=False)
                run_cmd(f"chown -R 1000:1000 '{dest_apps}'", as_root=True)

        if options.get('copy_folders'):
            folders = options.get('folders', [])
            yield "INFO", "Syncing mapped folders...", False
            for folder_entry in folders:
                if not folder_entry: continue
                name = folder_entry[0].strip() if isinstance(folder_entry, (list, tuple)) else str(folder_entry).strip()
                if not name: continue
                src_f = os.path.join(src_dir, name)
                dst_f = os.path.join(dest_dir, name)
                if os.path.exists(src_f):
                    yield "INFO", f"Syncing {name}...", False
                    sync_root_items(src_f, dst_f, move=False)
                    run_cmd(f"chown -R 1000:1000 '{dst_f}'", as_root=True)

    else: # ONLINE
        iso_name = f"{item['name']}.iso"
        dest_path = os.path.join(dest_dir, iso_name)
        iso_part = dest_path + ".part"

        for p in [iso_part, iso_part + ".sha256"]:
            if os.path.exists(p): os.remove(p)

        # Download ISO
        for res in _download_with_progress(item['iso_url'], iso_part, "Downloading image"):
            yield "INFO", res[0], res[1]

        # Verify
        with open(iso_part + ".sha256", 'w') as f: f.write(item['iso_hash'])
        if options.get('verify_dst'):
            yield "INFO", "Verifying downloaded image...", False
            ok, msg = verify_image_file(storage, iso_part)
            if not ok:
                yield "ERROR", f"ISO Download verification failed: {msg}", False
                return

        os.rename(iso_part, dest_path)
        if os.path.exists(iso_part + ".sha256"): os.rename(iso_part + ".sha256", dest_path + ".sha256")
        fname = iso_name

        # Download ZIP only if Use source settings or Update external apps is checked
        need_zip = bool(item.get('zip_url') and (options.get('use_settings') or options.get('update_apps', True)))
        if need_zip:
            updtemp = storage.updtemp_location
            if os.path.exists(updtemp):
                shutil.rmtree(updtemp, ignore_errors=True)
            os.makedirs(updtemp, exist_ok=True)

            zip_name = f"{item['name']}.zip"
            dest_zip = os.path.join(updtemp, zip_name)
            zip_part = dest_zip + ".part"

            for p in [zip_part, zip_part + ".sha256"]:
                if os.path.exists(p): os.remove(p)

            for res in _download_with_progress(item['zip_url'], zip_part, "Downloading external files"):
                yield "INFO", res[0], res[1]

            yield "INFO", "Verifying downloaded external files...", False
            with open(zip_part + ".sha256", 'w') as f: f.write(item['zip_hash'])
            if options.get('verify_dst'):
                ok, msg = verify_image_file(storage, zip_part)
                if not ok:
                    shutil.rmtree(updtemp, ignore_errors=True)
                    yield "ERROR", f"ZIP Download verification failed: {msg}", False
                    return

            os.rename(zip_part, dest_zip)
            if os.path.exists(zip_part + ".sha256"): os.rename(zip_part + ".sha256", dest_zip + ".sha256")

            yield "INFO", "Extracting external files...", False
            try:
                shutil.unpack_archive(dest_zip, updtemp)
            except Exception as e:
                shutil.rmtree(updtemp, ignore_errors=True)
                yield "ERROR", f"Failed to unzip external files: {e}", False
                return
            finally:
                if os.path.exists(dest_zip):
                    try: os.remove(dest_zip)
                    except Exception: pass
                if os.path.exists(dest_zip + ".sha256"):
                    try: os.remove(dest_zip + ".sha256")
                    except Exception: pass

            if options.get('use_settings'):
                src_ini = os.path.join(updtemp, "TempOS.ini")
                if os.path.exists(src_ini):
                    yield "INFO", "Updating settings (TempOS.ini)...", False
                    shutil.move(src_ini, os.path.join(dest_dir, "TempOS.ini"))

            if options.get('update_apps', True):
                src_apps = os.path.join(updtemp, "AppsExt")
                if os.path.exists(src_apps):
                    yield "INFO", "Updating external apps...", False
                    dest_apps = os.path.join(dest_dir, "AppsExt")
                    sync_root_items(src_apps, dest_apps, move=True)
                    run_cmd(f"chown -R 1000:1000 '{dest_apps}'", as_root=True)

            shutil.rmtree(updtemp, ignore_errors=True)

    # Common Logic for both Local and Online
    if options.get('boot_default'):
        yield "INFO", "Updating startup default...", False
        set_ventoy_default(storage, fname)

    if item['type'] == 'local' and item.get('mount_point') and os.path.ismount(item['mount_point']):
        yield "INFO", "Unmounting source...", False
        run_cmd(f"umount {item['mount_point']}", as_root=True)

    run_cmd("sync")

    # Cleanup Old Versions
    if options.get('cleanup_old'):
        yield "INFO", "Cleaning up old versions...", False
        time.sleep(2)
        try:
             storage.refresh()
             
             idx_installed = -1
             idx_running = -1
             
             for i, img in enumerate(storage.images):
                 if img['filename'] == fname:
                     idx_installed = i
                 if img['isCurrent']:
                     idx_running = i
             
             if idx_running == -1:
                 yield "INFO", "Currently running image not found in list (or not identified). Skipping cleanup.", False
             elif idx_installed == -1:
                 yield "INFO", "Installed image not found in list. Skipping cleanup.", False
             elif idx_installed >= idx_running:
                 yield "INFO", "Installed image is not newer than currently running version. Skipping cleanup.", False
             else:
                 start_delete = idx_running + 2
                 to_delete = storage.images[start_delete:]
                 
                 if to_delete:
                     for img in to_delete:
                         f_path = img['path']
                         try:
                             if os.path.exists(f_path):
                                 os.remove(f_path)
                                 yield "INFO", f"Deleted old version: {img['filename']}", False
                             
                             f_sha = f_path + ".sha256"
                             if os.path.exists(f_sha):
                                 os.remove(f_sha)
                         except Exception as e:
                             yield "INFO", f"Failed delete {img['filename']}: {e}", False
                 else:
                     yield "INFO", "No old versions found to delete.", False

        except Exception as e:
             yield "ERROR", f"Cleanup failed: {e}", False
    
    run_cmd("sync")
    yield "SUCCESS", "Update completed. Source drive can be removed.", False

def run_ventoy_update(device):
    yield "INFO", f"Updating Ventoy on {device}...", False
    ventoy_dir = "/Apps/Ventoy"
    if not os.path.exists(ventoy_dir):
        yield "ERROR", f"Ventoy tools not found at {ventoy_dir}", False
        return

    orig_cwd = os.getcwd()
    try:
        os.chdir(ventoy_dir)
        cmd = f"./Ventoy2Disk.sh -u {device}"
        out = run_cmd(cmd, as_root=True, input_text="y")
        if "successfully" in out or "Update Ventoy to" in out: 
             yield "SUCCESS", f"Ventoy updated on {device}", False
        else:
             yield "ERROR", f"Ventoy update failed output:\n{out}", False
    except Exception as e:
        yield "ERROR", f"Exception: {e}", False
    finally:
        os.chdir(orig_cwd)