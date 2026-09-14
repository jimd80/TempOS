import os
import shutil
import time
import hashlib
import re
from libtempos.utils import run_cmd
from libtempos.ops import MountTempOsBootMedium, verify_image_file, verify_booted_image, add_wifi_adaptors, create_bookmarks
from libtempos.NetInfo import NetInfo
from libtempos.update_logic import check_online_update

def run_startup_sequence(storage, settings):
    """
    A generator that performs the startup sequence steps.
    Yields tuples: (status_type, message, replace_last_line)
    status_type: 'INFO', 'SUCCESS', 'ERROR', 'WAIT_NET', 'WAIT_NET_WARN', 'UPDATE_AVAILABLE'
    Receives (via send): 'SKIP' to skip waiting for internet
    """
    yield "INFO", "Initiating Startup Sequence...", False
    run_cmd("xset s off -dpms")

    if not storage.boot_is_tempos:
        yield "ERROR", "This system is booted / installed directly as iso image. You can continue but things like update, external apps, mapped storage wil not work.", False
        return

    yield "INFO", "Init Boot Medium...", False
    success, msg = MountTempOsBootMedium(storage, unmount=False)
    
    if not success:
        yield "ERROR", f"Mount failed: {msg}", False
        return # Stop execution if mount fails
        
    # Copy settings files locally, in case of removable stick
    if not os.path.exists(storage.ini_filename) or not os.path.exists(storage.ventoy_filename):
        yield "ERROR", f"These files need to exist: {storage.ini_filename} and {storage.ventoy_filename}", False
        return

    shutil.copy2(storage.ini_filename, storage.ini_filename_local)
    shutil.copy2(storage.ventoy_filename, storage.ventoy_filename_local)
    
    yield "INFO", "Loading Settings...", False
    settings.load()

    # Configure Keyboard Layout (before wifi config)
    kb_setting = getattr(settings, 'kb_layout', '').strip()
    if kb_setting and kb_setting.lower() != 'be':
        if '-' in kb_setting:
            layout, variant = kb_setting.split('-', 1)
            cmd = f"setxkbmap -layout {layout} -variant {variant}"
        else:
            cmd = f"setxkbmap -layout {kb_setting}"
        yield "INFO", f"Configuring Keyboard Layout ({kb_setting})...", False
        run_cmd(cmd)

    # First load Wifi access points, as verify image can take a long time, so wifi can already connect
    yield "INFO", "Configuring WiFi...", False
    yield "SUCCESS", add_wifi_adaptors(settings), False

    # Verify image CRC
    if settings.verify_on_start:
        yield "INFO", "Verifying image...", False
        s, m = verify_booted_image(storage, os.path.join(storage.images_location, storage.tempos_image))
        yield ("SUCCESS" if s else "ERROR"), m, False

    # Chromium Policy Configuration
    if os.path.exists(storage.chrome_pol_tmpl):
        yield "INFO", "Configuring Browser Policies...", False
        try:
            tmp_pol = "/tmp/tempos_policy.json"
            with open(storage.chrome_pol_tmpl, "r") as f_in, open(tmp_pol, "w") as f_out:
                for line in f_in:
                    f_out.write(line.replace("{HomePageUrl}", settings.browser.get('home', ''))
                                   .replace("{StartPageUrl}", settings.browser.get('start', '')))
            
            dst_pol = "/etc/chromium/policies/managed/tempos_policy.json"
            run_cmd(f"mkdir -p {os.path.dirname(dst_pol)}", as_root=True)
            run_cmd(f"mv {tmp_pol} {dst_pol}", as_root=True)
            run_cmd(f"chown root:root {dst_pol}", as_root=True)
            run_cmd(f"chmod 644 {dst_pol}", as_root=True)
        except Exception as e:
            yield "ERROR", f"Policy setup failed: {e}", False

    # Chromium Bookmarks Configuration
    if os.path.exists(storage.chrome_bookmark_tmpl):
        yield "INFO", "Configuring Browser Bookmarks (System)...", False
        try:
            tmp_bm = "/tmp/initial_bookmarks.html"
            with open(storage.chrome_bookmark_tmpl, "r") as f_in, open(tmp_bm, "w") as f_out:
                for line in f_in:
                    if "{BookmarkUrl}" in line or "{BookmarkName}" in line:
                        for name, url in settings.favorites:
                            if name and url:
                                f_out.write(line.replace("{BookmarkName}", name).replace("{BookmarkUrl}", url))
                    else:
                        f_out.write(line)
            
            dst_bm = "/usr/share/chromium/initial_bookmarks.html"
            run_cmd(f"mkdir -p {os.path.dirname(dst_bm)}", as_root=True)
            run_cmd(f"mv {tmp_bm} {dst_bm}", as_root=True)
            run_cmd(f"chown root:root {dst_bm}", as_root=True)
            run_cmd(f"chmod 644 {dst_bm}", as_root=True)
        except Exception as e:
            yield "ERROR", f"Bookmarks setup failed: {e}", False

    # Direct User Bookmarks Configuration
    yield "INFO", "Configuring Browser Bookmarks (User)...", False
    try:
        create_bookmarks(storage, settings)
    except Exception as e:
        yield "ERROR", f"User bookmarks creation failed: {e}", False


    # Trusting Desktop Icons...
    if os.path.exists(storage.desktop_path):
        cnt = 0
        for f in os.listdir(storage.desktop_path):
            if f.endswith(".desktop"):
                fp = os.path.join(storage.desktop_path, f)
                os.chmod(fp, 0o755)
                with open(fp, "rb") as fh: sha = hashlib.sha256(fh.read()).hexdigest()
                run_cmd(["gio", "set", "-t", "string", fp, "metadata::xfce-exe-checksum", sha])
                cnt += 1
    
    if not storage.boot_removable:
        yield "INFO", "Mounting Persistent Folders...", False
        for name, mount in settings.folders:
            if not name or not mount: continue
            src = f"{storage.images_location}/{name.strip()}"
            dst = os.path.expanduser(mount.strip())
            
            if not os.path.exists(src):
                try: os.makedirs(src); yield "INFO", f"Created source: {src}", False
                except Exception as e: yield "INFO", f"Skip {name}: {e}", False
                
            if not os.path.exists(dst):
                try: os.makedirs(dst); yield "INFO", f"Created target: {dst}", False
                except Exception as e: yield "INFO", f"Skip {dst}: {e}", False
                
            run_cmd(["mount", "--bind", src, dst], as_root=True)
            if os.path.ismount(dst):
                yield "INFO", f"Mounted {name} -> {dst}", False
            else:
                yield "ERROR", f"Failed to mount {name} -> {dst}", False

        # AppsExt
        apps = f"{storage.images_location}/AppsExt"
        if os.path.exists(apps):
             yield "INFO", "Mounting AppsExt...", False
             run_cmd("mkdir -p /AppsExt", as_root=True)
             run_cmd("chmod 755 /AppsExt", as_root=True)
             run_cmd(["mount", "--bind", apps, "/AppsExt"], as_root=True)

             if not os.path.ismount("/AppsExt"):
                 yield "ERROR", "Failed to mount /AppsExt", False

             # Copy .desktop files from 1 level deep subdirectories
             yield "INFO", "Installing App desktop files...", False
             app_dest = os.path.join(storage.home, ".local/share/applications")
             if not os.path.exists(app_dest):
                 os.makedirs(app_dest, exist_ok=True)
             
             count_desktop = 0
             try:
                 for item in os.listdir("/AppsExt"):
                     subpath = os.path.join("/AppsExt", item)
                     if os.path.isdir(subpath):
                         for f in os.listdir(subpath):
                             if f.endswith(".desktop"):
                                 shutil.copy2(os.path.join(subpath, f), app_dest)
                                 count_desktop += 1
             except Exception as e:
                 yield "ERROR", f"Failed copying desktop files: {e}", False
             
             if count_desktop > 0:
                 yield "INFO", f"Copied {count_desktop} desktop files.", False
    else:
        yield "INFO", "Skipping persistent folders (System in RAM)", False

    # check and unlock internet
    connected = False
    if not settings.offline_mode:
        yield "INFO", "Waiting for Internet...", False
        net = NetInfo()
        i = 0
        tried_unlock = False
        warned_unknown_portal = False
        while True:
            net.check()
            if net.status == 2:
                connected = True
                break
            if net.status == 1 and net.portal_type != "Unknown" and not tried_unlock:
                yield "INFO", f"Captive portal detected ({net.portal_type}), unlocking...", False
                net.unlock_portal()
                tried_unlock = True
                if net.status == 2:
                    connected = True
                    break
                else:
                    yield "INFO", "Unlocking Captive portal failed.", False
            
            if net.status == 1:
                if not warned_unknown_portal and net.portal_type == "Unknown":
                    r_url = (net.redirect_url or "unknown URL")[:80]
                    yield "INFO", f"Unknown Wifi portal detected ({r_url})", False
                    warned_unknown_portal = True
                action = yield "WAIT_NET_WARN", "Please open browser and accept Wifi terms", True
            elif i >= 20:
                action = yield "WAIT_NET_WARN", "Please connect to internet! Waiting...", True
            else:
                action = yield "WAIT_NET", "Waiting for internet...", True
                
            if action == "SKIP":
                yield "INFO", "Internet wait skipped by user.", False
                break
            
            i += 1
        
        if connected:
             yield "SUCCESS", "Internet connected. Syncing time...", False
             run_cmd("ntpdate pool.ntp.org", as_root=True)
             yield "INFO", f"Current time: {run_cmd('date')}", False
             run_cmd(f"touch {storage.config_folder}/internet")
        else:
             yield "ERROR", f"No internet connection. Status: {net.status_text}", False

    # startup commands (can depend on internet)
    if settings.startup_cmds:
        yield "INFO", "Executing Startup Commands...", False
        for cmd in settings.startup_cmds:
            if cmd:
                yield "INFO", f"Exec: {cmd}", False
                run_cmd(cmd)

    if settings.browser.get('launch'):
        yield "INFO", "Launching Browser...", False
        run_cmd("nohup chromium >/dev/null 2>&1 &")

    if storage.boot_removable:
        yield "INFO", "Unmounting boot medium...", False
        ok, msg = MountTempOsBootMedium(storage, unmount=True)
        if ok: yield "INFO", "Please remove the USB drive (system is loaded in RAM)", False
        else: yield "ERROR", f"Unmount failed: {msg}", False

    # Check for online updates on start
    if settings.update_check and not storage.boot_removable and connected and settings.update_url:
        yield "INFO", "Checking for online updates...", False
        try:
            updates = check_online_update(settings.update_url)
            if updates:
                def _sort_key(s):
                    return re.sub(r'(\d+)', lambda m: m.group(1).zfill(10), str(s))

                updates.sort(key=lambda item: _sort_key(item['name']), reverse=True)
                latest_item = updates[0]
                
                current_name_raw = storage.tempos_image
                current_name = os.path.splitext(current_name_raw)[0] if current_name_raw else ""
                
                latest_key = _sort_key(latest_item['name'])
                curr_key = _sort_key(current_name)
                
                if latest_key > curr_key:
                    storage.available_update = latest_item
                    msg = f"A new version ({latest_item['name']}) is available."
                    yield "UPDATE_AVAILABLE", msg, False
        except Exception as e:
            yield "INFO", f"Update check failed: {e}", False

    yield "SUCCESS", "Startup sequence completed.", False