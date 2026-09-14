import os
import json
import time
import glob
import re
from libtempos.utils import run_cmd, to_mb

def _is_truthy(val):
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val == 1
    if isinstance(val, str):
        return val.strip().lower() in ('1', 'true', 'yes')
    return False

class StorageInfo:
    def __init__(self, waitboot=False):
        # constants
        self.home = os.environ.get("HOME", "/home/user")
        self.asset_folder = "/usr/share/tempos"
        
        self.ventoy_iso_device = "/dev/mapper/ventoy"
        self.mount_point_install = "/media/user/TempOsDestination"
        self.mount_point_update = "/media/user/TempOsSource"
        self.mount_point = "/media/user/TempOS"
        self.config_folder = f"{self.home}/.config/TempOS"
        self.ventoy_filename = f"{self.mount_point}/ventoy/ventoy.json"
        self.ini_filename_local = f"{self.config_folder}/TempOS.ini"
        self.ventoy_filename_local = f"{self.config_folder}/ventoy.json"
        self.desktop_path = run_cmd("xdg-user-dir DESKTOP").strip() or f"{self.home}/Desktop"
        self.chrome_pol_tmpl = f"{self.asset_folder}/tempos_policy.json.template"
        self.chrome_bookmark_tmpl = f"{self.asset_folder}/initial_bookmarks.html.template"
        self.available_update = None
        self.refresh(waitboot=waitboot)

    def refresh(self, waitboot=False):
        self.boot_removable = False
        self.boot_removable_failed = False
        self.boot_part_device = "" 
        self.boot_mount_device = "" 
        self.boot_main_device = "" 
        self.boot_is_tempos = False
        self.tempos_mount = ""
        self.partitions = []
        self.drives = []
        self.available_update = None

        df_out = run_cmd("df --output=fstype,source,target")
        live_medium_target = "/run/live/medium"
        is_tmpfs = False
        for line in df_out.splitlines():
            if live_medium_target in line and "tmpfs" in line.split()[0]:
                is_tmpfs = True
        self.boot_removable = is_tmpfs
        
        if not self.boot_removable and " toram" in run_cmd("cat /proc/cmdline"):
            self.boot_removable_failed = True

        max_attempts = 5 if waitboot else 1
        lsblk_json = ""
        for i in range(max_attempts):
            lsblk_json = run_cmd("lsblk -J -b -o PATH,KNAME,PKNAME,TYPE,FSTYPE,LABEL,MOUNTPOINT,SIZE,HOTPLUG,RM,PARTTYPENAME,FSUSED")
            if self.ventoy_iso_device in lsblk_json: break
            if i == 0 and waitboot: run_cmd("udevadm trigger", as_root=True)
            if i < max_attempts - 1:
                time.sleep(1)

        try:
            data = json.loads(lsblk_json)
        except Exception:
            data = {}
        devices = data.get('blockdevices', [])

        dm_dev = None
        for d in devices:
            path = d.get('path', '')
            if d.get('type') == 'dm' and '/dev/mapper/' in path and self.ventoy_iso_device not in path:
                dm_dev = d
                break
        
        if dm_dev:
            self.boot_mount_device = dm_dev.get('path', '')
            self.tempos_mount = dm_dev.get('mountpoint') or ""
            self.boot_part_device = self.boot_mount_device.replace('/dev/mapper/', '/dev/')
            
            real_part = next((x for x in devices if x.get('path') == self.boot_part_device), None)
            if not real_part:
                 name_part = self.boot_part_device.replace('/dev/', '')
                 real_part = next((x for x in devices if x.get('kname') == name_part), None)
            
            if real_part:
                pk = real_part.get('pkname')
                self.boot_main_device = f"/dev/{pk}" if pk else ""
                self.boot_is_tempos = (real_part.get('label', '') == 'TempOS')
        
        for d in devices:
            if d.get('type') != 'part' or d.get('fstype') == 'swap': continue
            path = d.get('path')
            is_tempos = 2 if path == self.boot_part_device else (1 if d.get('label') == 'TempOS' else 0)
            
            self.partitions.append({
                'PartitionDevice': path,
                'MainDevice': f"/dev/{d.get('pkname')}" if d.get('pkname') else "",
                'Size': d.get('size'),
                'FsType': d.get('fstype'),
                'IsMounted': bool(d.get('mountpoint')),
                'MountPoint': d.get('mountpoint'), # the mounted tempos path will not show up for /dev/sdx1 as it is in the hidden /dev/mapper/sdx1
                'IsTempOS': is_tempos,
                'VolumeName': d.get('label') or "",
                'Used': d.get('fsused')
            })

        for d in devices:
            if d.get('type') != 'disk' or d.get('fstype') == 'swap': continue
            path = d.get('path')
            parts_on_disk = [p for p in self.partitions if p['MainDevice'] == path]
            t_os_vals = [p['IsTempOS'] for p in parts_on_disk]
            is_removable = _is_truthy(d.get('hotplug')) or _is_truthy(d.get('rm'))
            self.drives.append({
                'MainDevice': path,
                'Size': d.get('size'),
                'IsRemovable': is_removable,
                'IsMounted': any(p['IsMounted'] for p in parts_on_disk),
                'TempOs': max(t_os_vals) if t_os_vals else 0,
                'VolumeNames': "+".join([p['VolumeName'] for p in parts_on_disk if p['VolumeName']])
            })

        # 0. Read Brand Info
        self.TempOs_Brand_Name = ""
        self.TempOs_Version = ""
        self.tempos_image = ""
        self.TempOs_Build_Date = ""
        self.distro_name = "Unknown"

        info_path = f"{self.config_folder}/info"
        if os.path.exists(info_path):
            try:
                with open(info_path, "r") as f:
                    for line in f:
                        if "=" in line:
                            key, val = line.strip().split("=", 1)
                            if key == "BrandName": self.TempOs_Brand_Name = val
                            elif key == "Version": self.TempOs_Version = val
                            elif key == "Image": self.tempos_image = val
                            elif key == "BuildDate": self.TempOs_Build_Date = val
            except Exception: pass

        if os.path.exists('/etc/debian_version'):
            try:
                with open('/etc/debian_version', 'r') as f:
                    self.distro_name = "Debian " + f.read().strip()
            except Exception: pass

        self.images_location = f"{self.mount_point}/{self.TempOs_Brand_Name}"
        self.updtemp_location = f"{self.images_location}/Update_Temp_Folder"
        self.ini_filename = f"{self.images_location}/TempOS.ini"
        
        # Scan for Images
        self.images = []
        if os.path.exists(self.images_location):
            files = glob.glob(f"{self.images_location}/*.iso")
            
            def sort_key(path):
                fname = os.path.basename(path)
                ver = []
                m = re.search(r'(\d+(?:\.\d+)+)', fname)
                if m:
                    try: ver = [int(p) for p in m.group(1).split('.')]
                    except: pass
                padded = re.sub(r'(\d+)', lambda m: m.group(1).zfill(10), fname)
                return (ver, padded)

            files.sort(key=sort_key, reverse=True)
            
            for i, path in enumerate(files):
                fname = os.path.basename(path)
                self.images.append({
                    'path': path,
                    'filename': fname,
                    'hasCrc': os.path.exists(path + ".sha256"),
                    'isCurrent': (fname == self.tempos_image),
                    'isLatest': (i == 0)
                })

    def get_summary(self):
        out = []
        out.append(f"Boot Removable: {self.boot_removable}")
        out.append(f"Boot to RAM Failed: {self.boot_removable_failed}")
        out.append(f"Boot Partition: {self.boot_part_device}")
        out.append(f"Boot Mount Device: {self.boot_mount_device}")
        out.append(f"Boot Drive: {self.boot_main_device}")
        out.append(f"Current System is TempOS: {self.boot_is_tempos}")
        out.append(f"TempOS Mount Point: {self.tempos_mount}")
        out.append(f"Desktop Path: {self.desktop_path}")
        return "\n".join(out)

    def get_tempos_summary(self):
        out = []
        out.append(f"Brand Name: {self.TempOs_Brand_Name}")
        out.append(f"Version: {self.TempOs_Version}")
        out.append(f"Image: {self.tempos_image}")
        out.append(f"Build Date: {self.TempOs_Build_Date}")
        out.append(f"Distro: {self.distro_name}")
        return "\n".join(out)

    def get_versions_summary(self):
        if not self.images: return "No images found."
        lines = []
        for img in self.images:
            info = []
            if img['isCurrent']: info.append("Current")
            if img['isLatest']: info.append("Latest")
            
            crc = "CRC Available" if img['hasCrc'] else "CRC Missing"
            extra = f" ({', '.join(info)})" if info else ""
            lines.append(f"{img['filename']} [{crc}]{extra}")
        return "\n".join(lines)

    def get_drive_summary(self):
        out = []
        out.append("Drives:")
        for d in self.drives:
            out.append(f" {d['MainDevice']} ({to_mb(d['Size'])} MB) Removable:{d['IsRemovable']} TempOS:{d['TempOs']} Labels:[{d['VolumeNames']}]")
        return "\n".join(out)

    def get_partition_summary(self):
        out = []
        out.append("Partitions:")
        for p in self.partitions:
            usage_str = ""
            if p.get('Size') and p.get('Used'):
                try:
                    pct = int(float(p['Used']) * 100 / float(p['Size']))
                    usage_str = f", {pct}% USED"
                except (ValueError, TypeError, ZeroDivisionError):
                    pass
            out.append(f" {p['PartitionDevice']} ({to_mb(p['Size'])} MB{usage_str}) FsType:{p['FsType']} IsTempOS:{p['IsTempOS']} VolumeName:{p['VolumeName']} MountPoint:{p['MountPoint']}")
        return "\n".join(out)

    def get_iso_options(self):
        return [f"/{self.TempOs_Brand_Name}/{img['filename']}" for img in self.images]