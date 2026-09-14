import os
import time
import shutil
import glob
import re
from libtempos.utils import run_cmd
from libtempos.ops import verify_image_file, sync_root_items

def mount_destination(device, mount_point):
    """Mounts the destination partition."""
    if os.path.ismount(mount_point):
        # Check if it's the right device
        find = run_cmd(f"findmnt -n -o SOURCE {mount_point}").strip()
        if find == device: return True
        run_cmd(f"umount {mount_point}", as_root=True)
        
    if not os.path.exists(mount_point):
        run_cmd(f"mkdir -p '{mount_point}'", as_root=True)
        run_cmd(f"chown 1000:1000 '{mount_point}'", as_root=True)
        
    run_cmd(f"mount {device} {mount_point} -o rw,uid=1000,gid=1000", as_root=True)
    run_cmd(f"chown 1000:1000 '{mount_point}'", as_root=True)
    return os.path.ismount(mount_point)

def run_install_sequence(storage, settings, options):
    """
    Generator for the installation process.
    options: {
        'drive': dict (from storage.drives),
        'iso_list': [path_strings],
        'mode': 'update' | 'erase',
        'update_ventoy': bool,
        'settings_mode': 'copy' | 'default' | 'keep',
        'copy_folders': bool,
        'copy_apps': bool,
        'boot_default': bool,
        'verify_src': bool,
        'verify_dst': bool
    }
    """
    dest_drive = options['drive']
    main_device = dest_drive['MainDevice']
    mount_point = storage.mount_point_install
    dest_ventoy_dir = f"{mount_point}/ventoy"
    dest_ventoy_json = f"{dest_ventoy_dir}/ventoy.json"
    
    # 1. Create list of ISOs
    isos_to_copy = options['iso_list']
    yield "INFO", f"Selected {len(isos_to_copy)} images for installation to {main_device}...", False

    # 2. Change dir to /Apps/Ventoy (for Ventoy2Disk.sh)
    ventoy_dir = "/Apps/Ventoy"
    if os.path.exists(ventoy_dir):
        os.chdir(ventoy_dir)
    else:
        yield "ERROR", f"Warning: {ventoy_dir} not found.", False
        return

    # 3. Verify source image
    if options['verify_src']:
        for iso_path in isos_to_copy:
            yield "INFO", f"Verifying source: {os.path.basename(iso_path)}...", False
            ok, msg = verify_image_file(storage, iso_path)
            if not ok:
                yield "ERROR", f"Source verification failed: {msg}", False
                return
            yield "INFO", f"Source {os.path.basename(iso_path)} verified successfully.", False

    # 4. Install / update ventoy
    if options['mode'] == 'erase':
        yield "INFO", f"Preparing {main_device} for clean install...", False
        
        # Unmount all partitions on this device
        for part in storage.partitions:
            if part['MainDevice'] == main_device and part['IsMounted']:
                yield "INFO", f"Unmounting {part['PartitionDevice']}...", False
                run_cmd(f"umount {part['PartitionDevice']}", as_root=True)
        
        run_cmd("sync")
        time.sleep(1)
        
        yield "INFO", f"Installing Ventoy to {main_device}...", False
        # Pass "y\ny\n" to stdin to answer the double confirmation prompts
        cmd = f"./Ventoy2Disk.sh -I {main_device} -L TempOS"
        out = run_cmd(cmd, as_root=True, input_text="y\ny")
        
        # Check output for success/fail roughly
        if "Install Ventoy to" not in out and "successfully" not in out:
            print(f"Ventoy install failed. Output:\n{out}")
            yield "ERROR", f"Ventoy installation failed. Output:\n{out}", False
            return

        yield "INFO", f"Waiting for dev to reappear...", False
        run_cmd("sync")
        time.sleep(8) # extra wait time for disk to re-appear
        storage.refresh()
        
    elif options['mode'] == 'update' and options['update_ventoy']:
        yield "INFO", f"Updating Ventoy on {main_device}...", False

        # Ventoy will possibly unmount anyway, so do it now
        for part in storage.partitions:
            if part['MainDevice'] == main_device and part['IsMounted']:
                run_cmd(f"umount {part['PartitionDevice']}", as_root=True)

        run_cmd("sync")
        time.sleep(1)
         
        cmd = f"./Ventoy2Disk.sh -u {main_device}"
        run_cmd(cmd, as_root=True, input_text="y")

        yield "INFO", f"Waiting for dev to reappear...", False
        run_cmd("sync")
        time.sleep(5)
        storage.refresh()

    # 5. Identify Partition (TempOS label)
    if options['mode'] == 'erase':
        # Find the new partition with label TempOS
        parts = [p for p in storage.partitions if p['MainDevice'] == main_device and p['VolumeName'] == 'TempOS']
        if not parts:
            yield "ERROR", "Could not find 'TempOS' partition after formatting.", False
            return
    else:
        # Update mode: find partition
        parts = [p for p in storage.partitions if p['MainDevice'] == main_device and p['IsTempOS'] == 1]
        if not parts:
            yield "ERROR", "Destination partition 'TempOS' not found.", False
            return

    target_part = parts[0]
    dest_device = target_part['PartitionDevice']
    
    # 6. Mount Destination is not already done before by user
    if not target_part['IsMounted']:
        yield "INFO", f"Mounting {dest_device} to {mount_point}...", False
        if not mount_destination(dest_device, mount_point):
            yield "ERROR", f"Failed to mount {dest_device} to {mount_point}", False
            return
    else:
        mount_point = target_part['MountPoint']
        dest_ventoy_dir = f"{mount_point}/ventoy"
        dest_ventoy_json = f"{dest_ventoy_dir}/ventoy.json"
        print (f"re-using mounted path {mount_point}")


    # 7. Create/Update ventoy dir with settings and theme
    if options['mode'] == 'erase' or options['update_ventoy']:
        if not os.path.exists(dest_ventoy_dir):
            os.makedirs(dest_ventoy_dir)
            run_cmd(f"chown 1000:1000 {dest_ventoy_dir}", as_root=True)
        else:
            # Delete contents of dest_ventoy_dir to ensure clean update of assets
            try:
                for item in os.listdir(dest_ventoy_dir):
                    item_path = os.path.join(dest_ventoy_dir, item)
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.unlink(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
            except Exception as e:
                yield "ERROR", f"Failed to clean destination Ventoy folder: {e}", False
                return

        # Configure Ventoy JSON from template
        target_iso_name = os.path.basename(isos_to_copy[0]) if isos_to_copy else storage.tempos_image
        iso_rel_path = f"/{storage.TempOs_Brand_Name}/{target_iso_name}"
        
        tmpl_path = os.path.join(storage.asset_folder, "ventoy.json.template")
        if os.path.exists(tmpl_path):
            try:
                with open(tmpl_path, 'r') as f: content = f.read()
                content = content.replace("{DefaultImage}", iso_rel_path)
                with open(dest_ventoy_json, 'w') as f: f.write(content)
                run_cmd(f"chown 1000:1000 {dest_ventoy_json}", as_root=True)
            except Exception as e:
                yield "ERROR", f"Failed to create /ventoy/ventoy.json: {e}", False
                return
        
        # Copy other Ventoy assets (themes, plugins)
        src_ventoy = os.path.join(storage.asset_folder, "ventoy")
        if os.path.exists(src_ventoy):
            yield "INFO", "Copying Ventoy assets...", False
            for item in os.listdir(src_ventoy):
                s = os.path.join(src_ventoy, item)
                d = os.path.join(dest_ventoy_dir, item)

                if os.path.isdir(s):
                    if os.path.exists(d): shutil.rmtree(d)
                    shutil.copytree(s, d)
                else:
                    shutil.copy2(s, d)

            run_cmd(f"chown -R 1000:1000 {dest_ventoy_dir}", as_root=True)

    # 8. Create Structure
    brand_dir = f"{mount_point}/{storage.TempOs_Brand_Name}"
    if not os.path.exists(brand_dir):
        os.makedirs(brand_dir)
        run_cmd(f"chown 1000:1000 {brand_dir}", as_root=True)

    # 9. Copy ISOs
    for iso_path in isos_to_copy:
        fname = os.path.basename(iso_path)
        dest_iso = f"{brand_dir}/{fname}"
        
        yield "INFO", f"Copying {fname}...", False
        try:
            shutil.copy2(iso_path, dest_iso)
            run_cmd("sync")
        except Exception as e:
            yield "ERROR", f"Copy failed: {e}", False
            return
            
        sha_src = iso_path + ".sha256"
        if os.path.exists(sha_src):
            shutil.copy2(sha_src, f"{brand_dir}/{fname}.sha256")
        
        run_cmd("sync")
        time.sleep(1)

    # 10. Settings
    yield "INFO", f"Applying settings ({options['settings_mode']})...", False
    dest_ini = f"{brand_dir}/TempOS.ini"

    if options['settings_mode'] == 'copy':
        if os.path.exists(storage.ini_filename_local):
            shutil.copy2(storage.ini_filename_local, dest_ini)
             
    elif options['settings_mode'] == 'default':
        shutil.copy2(f"{storage.asset_folder}/TempOS.ini.default", dest_ini)

    # 11. AppsExt
    if options.get('copy_apps', True):
        src_apps = f"{storage.images_location}/AppsExt"
        dst_apps = f"{brand_dir}/AppsExt"
        if os.path.exists(src_apps):
            yield "INFO", "Syncing AppsExt...", False
            sync_root_items(src_apps, dst_apps, move=False)
        elif options.get('mode') == 'erase':
            os.makedirs(dst_apps, exist_ok=True)
            
        if os.path.exists(dst_apps):
            readme_src = os.path.join(storage.asset_folder, "AppsExt_Readme.md")
            if os.path.exists(readme_src):
                shutil.copy2(readme_src, os.path.join(dst_apps, "Readme.md"))
            run_cmd(f"chown -R 1000:1000 '{dst_apps}'", as_root=True)

    # 12. Mapped Folders
    if options.get('copy_folders') and options.get('settings_mode') == 'copy':
        yield "INFO", "Syncing mapped folders...", False
        for name, _ in settings.folders:
            if not name: continue
            src_f = f"{storage.images_location}/{name.strip()}"
            dst_f = f"{brand_dir}/{name.strip()}"
            if os.path.exists(src_f):
                yield "INFO", f"Syncing {name}...", False
                sync_root_items(src_f, dst_f, move=False)
                run_cmd(f"chown -R 1000:1000 '{dst_f}'", as_root=True)

    # 13. Startup Default
    if options['boot_default']:
        yield "INFO", "Setting startup default...", False
        # Ensure ventoy.json exists
        if not os.path.exists(dest_ventoy_json):
             shutil.copy2(f"{storage.asset_folder}/ventoy.json.default", dest_ventoy_json)
        
        if os.path.exists(dest_ventoy_json):
            target_iso_name = os.path.basename(isos_to_copy[0])
            if not target_iso_name and storage.tempos_image: target_iso_name = storage.tempos_image
            
            iso_rel_path = f"/{storage.TempOs_Brand_Name}/{target_iso_name}"
            
            try:
                with open(dest_ventoy_json, 'r') as f: c = f.read()
                if "VTOY_DEFAULT_IMAGE" in c:
                    c = re.sub(r'("VTOY_DEFAULT_IMAGE":\s*)(.*?)(\s*\})', f'\\1"{iso_rel_path}"\\3', c, flags=re.DOTALL)
                    with open(dest_ventoy_json, 'w') as f: f.write(c)
            except Exception as e:
                yield "INFO", f"Failed to update ventoy.json: {e}", False

    run_cmd("sync")
    time.sleep(1)

    # 14. Verify Destination
    failed_images = []
    if options['verify_dst']:
        run_cmd("sh -c 'echo 3 > /proc/sys/vm/drop_caches'", as_root=True)
        
        for iso_path in isos_to_copy:
            fname = os.path.basename(iso_path)
            dst_iso = f"{brand_dir}/{fname}"
            yield "INFO", f"Verifying {fname}...", False
            ok, msg = verify_image_file(storage, dst_iso)
            if not ok:
                failed_images.append(fname)
                yield "INFO", f"Verification failed for {fname}: {msg}", False

    # 15. Cleanup
    yield "INFO", "Unmounting destination...", False
    run_cmd(f"umount {mount_point}", as_root=True)
    run_cmd("sync")
    
    if failed_images:
        failed_list = "\n  - ".join(failed_images)
        yield "ERROR", f"Installation completed but these images failed verification:\n  - {failed_list}", False
    elif dest_drive['IsRemovable']:
        yield "SUCCESS", f"Installation to {main_device} completed successfully. The drive can be removed.", False
    else:
        yield "SUCCESS", f"Installation to {main_device} completed successfully.", False