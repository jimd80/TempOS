import os
import re

class Settings:
    def __init__(self, storage):
        self.storage = storage
        self.default_iso = ""
        self.load()

    def load(self):
        self.load_from_file(self.storage.ini_filename_local)
        self._load_ventoy()

    def load_from_file(self, filepath):
        self.wifi = []
        self.browser = {"home": "", "start": "", "launch": False}
        self.favorites = []
        self.folders = []
        self.offline_mode = False
        self.startup_cmds = []
        self.verify_on_start = False
        self.update_check = False
        self.kb_layout = "be"
        self.close_startup_limit = None
        self.update_url = ""

        if os.path.exists(filepath):
            try:
                with open(filepath, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#') or '=' not in line: continue
                        k, v = line.split('=', 1)
                        k, v = k.strip().lower(), v.strip()
                        if k == 'wificonnect':
                            if '|' in v:
                                s, p = v.split('|', 1)
                                s, p = s.strip(), p.strip()
                                if s:  # SSID is mandatory, password is optional
                                    self.wifi.append([s, p])
                            else:
                                s = v.strip()
                                if s:
                                    self.wifi.append([s, ""])
                        elif k == 'favorite':
                            if ' ' in v:
                                n, u = v.rsplit(' ', 1)
                                n, u = n.strip(), u.strip()
                                if n and u:  # Both Name and URL are mandatory
                                    self.favorites.append([n, u])
                        elif k == 'folder':
                            if ' ' in v:
                                n, m = v.rsplit(' ', 1)
                                n, m = n.strip(), m.strip()
                                if n and m:  # Both Folder (USB) and Mount (local) are mandatory
                                    self.folders.append([n, m])
                        elif k == 'homepage': self.browser['home'] = v
                        elif k == 'startpage': self.browser['start'] = v
                        elif k == 'launchbrowser': self.browser['launch'] = (v == '1')
                        elif k == 'offlinemode': self.offline_mode = (v == '1')
                        elif k == 'verifyimage': self.verify_on_start = (v == '1')
                        elif k == 'updatecheck': self.update_check = (v == '1')
                        elif k == 'kblayout': self.kb_layout = v
                        elif k == 'startcmd': self.startup_cmds.append(v)
                        elif k == 'closestartup': 
                            try: self.close_startup_limit = int(v)
                            except: self.close_startup_limit = None
                        elif k == 'updateurl': self.update_url = v
            except Exception: pass

    def _load_ventoy(self):
        if os.path.exists(self.storage.ventoy_filename_local):
            try:
                with open(self.storage.ventoy_filename_local, 'r') as f: c = f.read()
                m = re.search(r'"VTOY_DEFAULT_IMAGE":\s*"([^"]+)"', c)
                if m: self.default_iso = m.group(1)
            except: pass

    def save(self):
        out = []
        for item in self.wifi:
            s = item[0].strip() if len(item) > 0 and item[0] else ""
            p = item[1].strip() if len(item) > 1 and item[1] else ""
            if s:  # SSID mandatory, password optional
                out.append(f"wificonnect={s}|{p}")
        out.append(f"homepage={self.browser['home']}")
        out.append(f"startpage={self.browser['start']}")
        out.append(f"launchbrowser={1 if self.browser['launch'] else 0}")
        out.append(f"offlinemode={1 if self.offline_mode else 0}")
        out.append(f"verifyimage={1 if self.verify_on_start else 0}")
        out.append(f"updatecheck={1 if self.update_check else 0}")
        out.append(f"kblayout={self.kb_layout if self.kb_layout else 'be'}")
        out.append(f"closestartup={self.close_startup_limit if self.close_startup_limit is not None else ''}")
        out.append(f"updateurl={self.update_url}")
        for item in self.favorites:
            n = item[0].strip() if len(item) > 0 and item[0] else ""
            u = item[1].strip() if len(item) > 1 and item[1] else ""
            if n and u:  # Both mandatory
                out.append(f"favorite={n} {u}")
        for item in self.folders:
            n = item[0].strip() if len(item) > 0 and item[0] else ""
            m = item[1].strip() if len(item) > 1 and item[1] else ""
            if n and m:  # Both mandatory
                out.append(f"folder={n} {m}")
        for cmd in self.startup_cmds: out.append(f"startcmd={cmd}")
        text = "\n".join(out)
        
        self._save_ventoy(self.storage.ventoy_filename_local)
        
        try:
            with open(self.storage.ini_filename_local, 'w') as f: f.write(text)
        except Exception as e: return f"Err Local: {e}"
        
        if self.storage.boot_is_tempos and os.path.ismount(self.storage.mount_point):
            try:
                self._save_ventoy(self.storage.ventoy_filename)
                d = os.path.dirname(self.storage.ini_filename)
                if not os.path.exists(d): os.makedirs(d)
                with open(self.storage.ini_filename, 'w') as f: f.write(text)
                return "Saved to System and USB."
            except Exception as e: return f"Saved Local. Err USB: {e}"
        return "Saved to System (USB not mounted)"

    def _save_ventoy(self, path):
        if not os.path.exists(path) or not self.default_iso: return
        try:
            with open(path, 'r') as f: c = f.read()
            c = re.sub(r'("VTOY_DEFAULT_IMAGE":\s*)(.*?)(\s*\})', f'\\1"{self.default_iso}"\\3', c, flags=re.DOTALL)
            with open(path, 'w') as f: f.write(c)
        except Exception as e: print(f"Ventoy save err: {e}")