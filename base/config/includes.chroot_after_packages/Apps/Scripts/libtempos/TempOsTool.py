import os
import re
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from libtempos.utils import run_cmd
from libtempos.NetInfo import NetInfo
from libtempos.MemInfo import MemInfo
from libtempos.StorageInfo import StorageInfo
from libtempos.Settings import Settings
from libtempos.ops import verify_booted_image, add_wifi_adaptors, MountTempOsBootMedium, perform_power_action
from libtempos.install_logic import run_install_sequence
from libtempos.update_logic import check_online_update, check_partition_update, run_update_sequence, run_ventoy_update

class LogDialog(tk.Toplevel):
    """
    A modal dialog that displays a scrolling log of operations.
    Replaces the ephemeral progress overlay.
    """
    def __init__(self, parent, title):
        super().__init__(parent)
        self.title(title)
        
        ws, hs = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 700, 500
        x, y = int((ws - w) / 2), int((hs - h) / 2)
        self.geometry(f"{w}x{h}+{x}+{y}")
        
        # Modal behavior
        self.transient(parent)
        self.grab_set()
        
        # Log area
        self.log = scrolledtext.ScrolledText(self, wrap=tk.WORD, font=("Consolas", 10))
        self.log.pack(expand=True, fill='both', padx=5, pady=5)
        self.log.tag_config("ERROR", foreground="red")
        self.log.tag_config("SUCCESS", foreground="green")
        self.log.tag_config("INFO", foreground="black")
        
        # Button Frame
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill='x', pady=5, padx=5)
        
        # Close Button
        self.btn_close = ttk.Button(btn_frame, text="Close", command=self.destroy, state='disabled')
        self.btn_close.pack(side='right')

        # Reboot Button (Hidden by default, used for updates)
        self.btn_reboot = ttk.Button(btn_frame, text="Reboot System", command=self.on_reboot)
        # We don't pack it yet

        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.is_finished = False

    def on_reboot(self):
        self.destroy()
        self.master.destroy()
        perform_power_action("reboot")

    def add_line(self, status, message, replace=False):
        if replace:
            # Delete the last line (end-2l to end-1c covers the last content line before the final newline)
            self.log.delete("end-2l", "end-1c")
            
        tag = status if status in ["ERROR", "SUCCESS", "INFO"] else "INFO"
        self.log.insert(tk.END, f"[{status}] {message}\n", tag)
        self.log.see(tk.END)
        self.update()

    def finish(self, success=False, show_reboot=False):
        self.is_finished = True
        self.log.insert(tk.END, "--- Operation Finished ---\n", "INFO")
        self.log.see(tk.END)
        self.btn_close.configure(state='normal')
        
        if success and show_reboot:
            self.btn_reboot.pack(side='right', padx=10)

    def on_closing(self):
        if self.is_finished:
            self.destroy()
        else:
            messagebox.showwarning("Busy", "Operation in progress. Please wait.")

class TempOsTool(tk.Tk):
    def __init__(self, net, mem, storage, settings):
        super().__init__()
        self.title("TempOS Tools")
        ws, hs = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 1000, 700
        x, y = int((ws - w) / 2), int((hs - h) / 2)
        self.geometry(f"{w}x{h}+{x}+{y}")
        
        self.net = net
        self.mem = mem
        self.storage = storage
        self.settings = settings
        
        self.updates_found = []
        self.update_map = []
        
        self._build_ui()

    def _get_mapped_folders_suffix(self):
        names = [f[0].strip() for f in self.settings.folders if f and len(f) > 0 and f[0].strip()]
        if not names:
            return ""
        joined = ", ".join(names)
        if len(joined) > 100:
            joined = joined[:97] + "..."
        return f" ({joined})"

    def _build_ui(self):
        # Configure styles
        style = ttk.Style()
        # Ensure readonly comboboxes look white (enabled) instead of gray
        style.map('TCombobox', fieldbackground=[('readonly', 'white')])
        style.map('TCombobox', selectbackground=[('readonly', 'white')])
        style.map('TCombobox', selectforeground=[('readonly', 'black')])

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(expand=True, fill='both')

        # Tabs
        self.tab_info = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_info, text='System Info')
        
        self.tab_settings = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_settings, text='Settings')

        self.tab_install = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_install, text='Install to')
        
        self.tab_update = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_update, text='Update')

        self.tab_packages = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_packages, text='Packages')

        self.tab_debug = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_debug, text='Debug')

        self.tab_log = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_log, text='Startup Log')

        self._build_info_tab()
        self._build_settings_tab()
        self._build_install_tab()
        self._build_update_tab()
        self._build_packages_tab()
        self._build_debug_tab()
        self._build_log_tab()

    def _build_info_tab(self):
        info_top = ttk.Frame(self.tab_info)
        info_top.pack(fill='x', padx=5, pady=5)
        
        self.lbl_ver = ttk.Label(info_top, font=("TkDefaultFont", 10, "bold"))
        self.lbl_ver.pack(side='left')

        ttk.Button(info_top, text="Refresh", command=self.on_refresh_info).pack(side='right')
        ttk.Button(info_top, text="Verify image", command=self.on_verify_image).pack(side='right', padx=5)

        self.info_text = scrolledtext.ScrolledText(self.tab_info, wrap=tk.WORD, font=("Consolas", 10))
        self.info_text.pack(expand=True, fill='both', padx=5, pady=5)
        self._update_info_text()

    def _build_settings_tab(self):
        pad = {'padx': 5, 'pady': 5}
        self.tab_settings.columnconfigure(0, weight=1)
        self.tab_settings.columnconfigure(1, weight=1)
        self.tab_settings.rowconfigure(2, weight=1)
        
        # --- Row 0: General ---
        frm_top = ttk.Frame(self.tab_settings)
        frm_top.grid(row=0, column=0, columnspan=2, sticky='ew', **pad)
        
        # Line 1: Default boot image & Keyboard layout
        f_iso = ttk.Frame(frm_top); f_iso.pack(fill='x', pady=2)
        ttk.Label(f_iso, text="Default boot image:").pack(side='left')
        self.cb_iso = ttk.Combobox(f_iso, values=self.storage.get_iso_options(), width=35)
        self.cb_iso.pack(side='left', padx=5)

        ttk.Label(f_iso, text="Keyboard layout:").pack(side='left', padx=(15, 5))
        self.cb_kblayout = ttk.Combobox(f_iso, values=["AZERTY (System default)", "QWERTY"], state='readonly', width=22)
        self.cb_kblayout.pack(side='left', padx=5)

        # Line 2: Offline, Close, Verify, Update Check
        f1 = ttk.Frame(frm_top); f1.pack(fill='x', pady=2)
        
        self.var_offline = tk.BooleanVar()
        ttk.Checkbutton(f1, text="Offline mode", variable=self.var_offline).pack(side='left', padx=(5, 10))
        
        ttk.Label(f1, text="Close Startup (sec):").pack(side='left')
        self.ent_closestartup = ttk.Entry(f1, width=5); self.ent_closestartup.pack(side='left', padx=(5, 10))

        self.var_verify = tk.BooleanVar()
        ttk.Checkbutton(f1, text="Verify image on start", variable=self.var_verify).pack(side='left', padx=10)

        self.var_updatecheck = tk.BooleanVar()
        ttk.Checkbutton(f1, text="Check for update on start", variable=self.var_updatecheck).pack(side='left', padx=10)
        
        # --- Row 1: WiFi & Folders ---
        # Wi-Fi
        frm_wifi = ttk.LabelFrame(self.tab_settings, text="Auto Connect WiFi")
        frm_wifi.grid(row=1, column=0, sticky='nsew', **pad)
        
        cols = ("ssid", "pass")
        self.tv_wifi = ttk.Treeview(frm_wifi, columns=cols, show='headings', height=5)
        self.tv_wifi.heading("ssid", text="SSID"); self.tv_wifi.column("ssid", width=120)
        self.tv_wifi.heading("pass", text="Password"); self.tv_wifi.column("pass", width=120)
        self.tv_wifi.pack(fill='both', expand=True, **pad)
        
        frm_wifi_in = ttk.Frame(frm_wifi)
        frm_wifi_in.pack(fill='x', **pad)
        ttk.Label(frm_wifi_in, text="SSID:").pack(side='left')
        self.ent_ssid = ttk.Entry(frm_wifi_in, width=10); self.ent_ssid.pack(side='left', padx=2)
        ttk.Label(frm_wifi_in, text="Pass:").pack(side='left')
        self.ent_pass = ttk.Entry(frm_wifi_in, width=10); self.ent_pass.pack(side='left', padx=2)
        ttk.Button(frm_wifi_in, text="+", width=3, command=self.add_wifi).pack(side='left')
        ttk.Button(frm_wifi_in, text="-", width=3, command=lambda: self.del_item(self.tv_wifi)).pack(side='left')

        self.ent_ssid.bind('<Return>', lambda e: self.add_wifi())
        self.ent_pass.bind('<Return>', lambda e: self.add_wifi())

        # Persistent Folders
        frm_fold = ttk.LabelFrame(self.tab_settings, text="Map Persistent Folders")
        frm_fold.grid(row=1, column=1, sticky='nsew', **pad)
        
        cols_fold = ("name", "mount")
        self.tv_fold = ttk.Treeview(frm_fold, columns=cols_fold, show='headings', height=5)
        self.tv_fold.heading("name", text="Folder (USB)"); self.tv_fold.column("name", width=120)
        self.tv_fold.heading("mount", text="Mount (Local)"); self.tv_fold.column("mount", width=180)
        self.tv_fold.pack(fill='both', expand=True, **pad)

        frm_fold_in = ttk.Frame(frm_fold)
        frm_fold_in.pack(fill='x', **pad)
        ttk.Label(frm_fold_in, text="Folder:").pack(side='left')
        self.ent_fold_n = ttk.Entry(frm_fold_in, width=10); self.ent_fold_n.pack(side='left', padx=2)
        ttk.Label(frm_fold_in, text="Mount:").pack(side='left')
        self.ent_fold_m = ttk.Entry(frm_fold_in, width=10); self.ent_fold_m.pack(side='left', padx=2)
        ttk.Button(frm_fold_in, text="+", width=3, command=self.add_fold).pack(side='left')
        ttk.Button(frm_fold_in, text="-", width=3, command=lambda: self.del_item(self.tv_fold)).pack(side='left')

        self.ent_fold_n.bind('<Return>', lambda e: self.add_fold())
        self.ent_fold_m.bind('<Return>', lambda e: self.add_fold())

        # --- Row 2: Browser & Startup Cmds ---
        # Browser
        frm_brow = ttk.LabelFrame(self.tab_settings, text="Browser")
        frm_brow.grid(row=2, column=0, sticky='nsew', **pad)
        
        f_b_top = ttk.Frame(frm_brow); f_b_top.pack(fill='x', **pad)
        ttk.Label(f_b_top, text="Home:").grid(row=0, column=0, sticky='e')
        self.ent_home = ttk.Entry(f_b_top); self.ent_home.grid(row=0, column=1, sticky='ew')
        ttk.Label(f_b_top, text="Start:").grid(row=1, column=0, sticky='e')
        self.ent_start = ttk.Entry(f_b_top); self.ent_start.grid(row=1, column=1, sticky='ew')
        self.var_launch = tk.BooleanVar()
        ttk.Checkbutton(f_b_top, text="Launch on startup", variable=self.var_launch).grid(row=2, column=1, sticky='w')
        f_b_top.columnconfigure(1, weight=1)

        ttk.Label(frm_brow, text="Favorites:").pack(anchor='w', padx=5)
        cols_fav = ("name", "url")
        self.tv_fav = ttk.Treeview(frm_brow, columns=cols_fav, show='headings', height=3)
        self.tv_fav.heading("name", text="Name"); self.tv_fav.column("name", width=100)
        self.tv_fav.heading("url", text="URL"); self.tv_fav.column("url", width=200)
        self.tv_fav.pack(fill='both', expand=True, **pad)
        
        frm_fav_in = ttk.Frame(frm_brow)
        frm_fav_in.pack(fill='x', **pad)
        ttk.Label(frm_fav_in, text="Name:").pack(side='left')
        self.ent_fav_n = ttk.Entry(frm_fav_in, width=8); self.ent_fav_n.pack(side='left', padx=2)
        ttk.Label(frm_fav_in, text="URL:").pack(side='left')
        self.ent_fav_u = ttk.Entry(frm_fav_in, width=15); self.ent_fav_u.pack(side='left', padx=2)
        ttk.Button(frm_fav_in, text="+", width=3, command=self.add_fav).pack(side='left')
        ttk.Button(frm_fav_in, text="-", width=3, command=lambda: self.del_item(self.tv_fav)).pack(side='left')

        self.ent_fav_n.bind('<Return>', lambda e: self.add_fav())
        self.ent_fav_u.bind('<Return>', lambda e: self.add_fav())

        # Startup Commands
        frm_cmd = ttk.LabelFrame(self.tab_settings, text="Startup Commands")
        frm_cmd.grid(row=2, column=1, sticky='nsew', **pad)
        self.txt_cmd = scrolledtext.ScrolledText(frm_cmd, width=30, height=10)
        self.txt_cmd.pack(fill='both', expand=True, **pad)

        # --- Row 3: Update URL ---
        frm_update = ttk.Frame(self.tab_settings)
        frm_update.grid(row=3, column=0, columnspan=2, sticky='ew', **pad)
        ttk.Label(frm_update, text="Update URL:").pack(side='left')
        self.ent_updateurl = ttk.Entry(frm_update); self.ent_updateurl.pack(side='left', fill='x', expand=True, padx=5)

        # Save Button Area (Row 4)
        frm_bot = ttk.Frame(self.tab_settings)
        frm_bot.grid(row=4, column=0, columnspan=2, pady=10, sticky='ew')
        
        frm_btns = ttk.Frame(frm_bot)
        frm_btns.pack()
        
        ttk.Button(frm_btns, text="Reload", command=self.on_reload).pack(side='left', padx=5)
        ttk.Button(frm_btns, text="Load Defaults", command=self.on_load_defaults).pack(side='left', padx=5)
        
        self.btn_save = ttk.Button(frm_btns, text="Save Settings", command=self.save_settings)
        self.btn_save.pack(side='left', padx=5)
        
        self.lbl_save_status = ttk.Label(frm_bot, text="", foreground="red")
        self.lbl_save_status.pack(pady=5)
        
        self.load_ui_from_settings()

    def _build_install_tab(self):
        pad = {'padx': 5, 'pady': 5}

        # Top Container for Source and Actions
        frm_top = ttk.Frame(self.tab_install)
        frm_top.pack(fill='x', **pad)

        # 1. Source (Left)
        frm_src = ttk.LabelFrame(frm_top, text="Image Source")
        frm_src.pack(side='left', fill='both', expand=True, padx=(0, 5))
        
        self.var_install_all = tk.BooleanVar(value=False)
        self.chk_install_all = ttk.Checkbutton(frm_src, text="Install all images", variable=self.var_install_all, command=self._update_install_ui_state)
        self.chk_install_all.pack(anchor='w', **pad)
        
        frm_iso = ttk.Frame(frm_src)
        frm_iso.pack(fill='x', padx=5, pady=(0,5))
        ttk.Label(frm_iso, text="Install this image:").pack(side='left')
        self.cb_install_iso = ttk.Combobox(frm_iso, state='readonly', width=50)
        self.cb_install_iso.pack(side='left', padx=5, fill='x', expand=True)

        # Buttons (Right of Source)
        frm_btns = ttk.Frame(frm_top)
        frm_btns.pack(side='right', fill='y')

        self.btn_install = ttk.Button(frm_btns, text="Start Installation", command=self.on_start_install)
        self.btn_install.pack(side='top', fill='x', pady=(0, 5))

        ttk.Button(frm_btns, text="Refresh", command=self._refresh_install_destinations).pack(side='top', fill='x')

        # 2. Destination
        frm_dest = ttk.LabelFrame(self.tab_install, text="Destination")
        frm_dest.pack(fill='both', expand=True, **pad)
        
        cols = ("device", "type", "size", "volume")
        self.tv_dest = ttk.Treeview(frm_dest, columns=cols, show='headings', height=5, selectmode='browse')
        self.tv_dest.heading("device", text="Device")
        self.tv_dest.column("device", width=80)
        self.tv_dest.heading("type", text="Type")
        self.tv_dest.column("type", width=120)
        self.tv_dest.heading("size", text="Size")
        self.tv_dest.column("size", width=100, anchor='e')
        self.tv_dest.heading("volume", text="Volume")
        self.tv_dest.column("volume", width=200)
        
        self.tv_dest.pack(fill='both', expand=True, padx=5, pady=5)
        self.tv_dest.bind('<<TreeviewSelect>>', self._on_dest_select)

        # 3. Mode & Settings
        frm_opts = ttk.Frame(self.tab_install)
        frm_opts.pack(fill='x', **pad)
        
        # -- Installation Mode --
        frm_mode = ttk.LabelFrame(frm_opts, text="Installation Mode")
        frm_mode.pack(fill='x', anchor='w', **pad)
        
        self.var_install_mode = tk.StringVar(value="update")
        self.rb_update = ttk.Radiobutton(frm_mode, text="Update", variable=self.var_install_mode, value="update", command=self._update_install_ui_state)
        self.rb_update.pack(side='left', padx=10, pady=5)
        
        self.rb_erase = ttk.Radiobutton(frm_mode, text="Full erase and (re)install", variable=self.var_install_mode, value="erase", command=self._update_install_ui_state)
        self.rb_erase.pack(side='left', padx=10, pady=5)
        
        # Checkbox Update Ventoy
        self.var_update_ventoy = tk.BooleanVar(value=True)
        self.chk_ventoy = ttk.Checkbutton(frm_opts, text="Update Ventoy", variable=self.var_update_ventoy)
        self.chk_ventoy.pack(anchor='w', padx=20, pady=2)

        # -- Settings --
        frm_set = ttk.LabelFrame(frm_opts, text="Settings")
        frm_set.pack(fill='x', anchor='w', **pad)
        
        self.var_settings_mode = tk.StringVar(value="copy")
        ttk.Radiobutton(frm_set, text="Copy settings", variable=self.var_settings_mode, value="copy").pack(side='left', padx=10, pady=5)
        ttk.Radiobutton(frm_set, text="Default settings", variable=self.var_settings_mode, value="default").pack(side='left', padx=10, pady=5)
        self.rb_keep_settings = ttk.Radiobutton(frm_set, text="Keep destination settings", variable=self.var_settings_mode, value="keep")
        self.rb_keep_settings.pack(side='left', padx=10, pady=5)
        
        # Checkbox Copy Folders
        self.var_copy_folders = tk.BooleanVar(value=False)
        self.chk_copy_folders = ttk.Checkbutton(frm_opts, text=f"Copy (overwrite) mapped folders{self._get_mapped_folders_suffix()}", variable=self.var_copy_folders)
        self.chk_copy_folders.pack(anchor='w', padx=20, pady=2)

        # Checkbox Copy External Apps
        self.var_copy_apps = tk.BooleanVar(value=True)
        self.chk_copy_apps = ttk.Checkbutton(frm_opts, text="Copy (update) external apps", variable=self.var_copy_apps)
        self.chk_copy_apps.pack(anchor='w', padx=20, pady=2)

        # Checkbox Startup Default
        self.var_boot_default = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_opts, text="Make startup default (latest version if install all)", variable=self.var_boot_default).pack(anchor='w', padx=20, pady=2)

        # -- Verify --
        frm_verify = ttk.Frame(frm_opts)
        frm_verify.pack(fill='x', anchor='w', padx=20, pady=2)
        
        self.var_verify_src = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_verify, text="Verify source image", variable=self.var_verify_src).pack(side='left', padx=(0, 10))
        
        self.var_verify_dst = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_verify, text="Verify installed image", variable=self.var_verify_dst).pack(side='left', padx=10)

        self._populate_isos()
        self._refresh_install_destinations()
        self._update_install_ui_state()

    def _build_update_tab(self):
        pad = {'padx': 5, 'pady': 5}
        
        # Online Update
        frm_online = ttk.LabelFrame(self.tab_update, text="Online Update")
        frm_online.pack(fill='x', **pad)
        
        ttk.Label(frm_online, text="URL:").pack(side='left', **pad)
        self.ent_update_tab_url = ttk.Entry(frm_online, width=50)
        self.ent_update_tab_url.pack(side='left', fill='x', expand=True, **pad)
        self.ent_update_tab_url.insert(0, self.settings.update_url)
        
        ttk.Button(frm_online, text="Check for update", command=self.on_check_online_update).pack(side='left', **pad)
        
        # Partition / USB Update
        frm_part = ttk.LabelFrame(self.tab_update, text="Update from USB drive (please insert)")
        frm_part.pack(fill='x', **pad)
        
        ttk.Label(frm_part, text="Select Partition:").pack(side='left', **pad)
        self.cb_update_part = ttk.Combobox(frm_part, state='readonly', width=30)
        self.cb_update_part.pack(side='left', **pad)
        
        ttk.Button(frm_part, text="Refresh", command=self._refresh_update_partitions).pack(side='left', **pad)
        ttk.Button(frm_part, text="Check for update", command=self.on_check_part_update).pack(side='left', **pad)
        
        # Update Config
        frm_config = ttk.LabelFrame(self.tab_update, text="Update Config")
        frm_config.pack(fill='x', **pad)
        
        # Row 1: System Info
        f_info = ttk.Frame(frm_config)
        f_info.pack(fill='x', **pad)
        
        ttk.Label(f_info, text="Current: ", font=("TkDefaultFont", 10)).pack(side='left')
        ver_text = f"{self.storage.TempOs_Brand_Name} version {self.storage.TempOs_Version} ({self.storage.TempOs_Build_Date})"
        ttk.Label(f_info, text=ver_text, font=("TkDefaultFont", 10, "bold")).pack(side='left')
        
        # Row 2: Status
        self.lbl_update_status = ttk.Label(frm_config, text="Please press check for update", font=("TkDefaultFont", 10, "bold"))
        self.lbl_update_status.pack(fill='x', **pad)

        # Row 3: Available Version Dropdown
        frm_sel = ttk.Frame(frm_config)
        frm_sel.pack(fill='x', **pad)
        ttk.Label(frm_sel, text="Available Version:").pack(side='left')
        self.cb_update_version = ttk.Combobox(frm_sel, state='readonly', width=50)
        self.cb_update_version.pack(side='left', fill='x', expand=True, padx=5)
        self.cb_update_version.bind('<<ComboboxSelected>>', self._on_update_ver_select)
        
        # Row 4: Checkboxes
        frm_chk = ttk.Frame(frm_config)
        frm_chk.pack(fill='x', **pad)
        
        self.var_upd_verify_src = tk.BooleanVar(value=False)
        self.var_upd_verify_dst = tk.BooleanVar(value=True)
        self.var_upd_settings = tk.BooleanVar(value=False)
        self.var_upd_apps = tk.BooleanVar(value=True)
        self.var_upd_folders = tk.BooleanVar(value=False)
        self.var_upd_default = tk.BooleanVar(value=True)
        self.var_cleanup_old = tk.BooleanVar(value=True)
        
        ttk.Checkbutton(frm_chk, text="Verify source image", variable=self.var_upd_verify_src).pack(anchor='w')
        ttk.Checkbutton(frm_chk, text="Verify installed image", variable=self.var_upd_verify_dst).pack(anchor='w')
        ttk.Checkbutton(frm_chk, text="Use source settings (replaces current)", variable=self.var_upd_settings).pack(anchor='w')
        self.chk_upd_apps = ttk.Checkbutton(frm_chk, text="Update external apps", variable=self.var_upd_apps)
        self.chk_upd_apps.pack(anchor='w')
        self.chk_upd_folders = ttk.Checkbutton(frm_chk, text=f"Copy mapped folders{self._get_mapped_folders_suffix()}", variable=self.var_upd_folders)
        self.chk_upd_folders.pack(anchor='w')
        ttk.Checkbutton(frm_chk, text="Set as startup default", variable=self.var_upd_default).pack(anchor='w')
        ttk.Checkbutton(frm_chk, text="Remove old versions (keep 1 version below current)", variable=self.var_cleanup_old).pack(anchor='w')

        # NEW Group: Update
        frm_exec = ttk.LabelFrame(self.tab_update, text="Update")
        frm_exec.pack(fill='x', **pad)

        self.lbl_update_target = ttk.Label(frm_exec, text="Please press [Check for update]", font=("TkDefaultFont", 10, "bold"))
        self.lbl_update_target.pack(fill='x', **pad)

        self.btn_start_update = ttk.Button(frm_exec, text="Start Update", command=self.on_start_update, state='disabled')
        self.btn_start_update.pack(side='left', **pad)
        
        ttk.Button(frm_exec, text="Update Ventoy (do this after reboot)", command=self.on_ventoy_update).pack(side='right', **pad)

        self._refresh_update_partitions()

    def _build_packages_tab(self):
        frame = ttk.Frame(self.tab_packages)
        frame.pack(fill='both', expand=True, padx=5, pady=5)
        
        # Toolbar
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill='x', pady=5)
        
        lbl_count = ttk.Label(toolbar, text="")
        lbl_count.pack(side='left')
        self.lbl_pkg_count = lbl_count
        
        ttk.Button(toolbar, text="Refresh", command=self._refresh_packages).pack(side='right')
        
        # Treeview
        cols = ("name", "size")
        self.tv_packages = ttk.Treeview(frame, columns=cols, show='headings')
        
        self.tv_packages.heading("name", text="Package Name", command=lambda: self._sort_tv(self.tv_packages, "name", False))
        self.tv_packages.column("name", width=600)
        
        self.tv_packages.heading("size", text="Size (MB)", command=lambda: self._sort_tv(self.tv_packages, "size", False, float))
        self.tv_packages.column("size", width=150, anchor='e')
        
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tv_packages.yview)
        self.tv_packages.configure(yscrollcommand=vsb.set)
        
        self.tv_packages.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        
        # Initial load delayed to let UI render
        self.after(100, self._refresh_packages)

    def _refresh_packages(self):
        # Quick popup just to say loading
        self._set_ui_busy(True)
        self.update()
        try:
            for item in self.tv_packages.get_children():
                self.tv_packages.delete(item)
            
            out = run_cmd("dpkg-query -W -f='${Package} ${Installed-Size}\n'")
            if not out: return
            
            data = []
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    name = parts[0]
                    try:
                        size_mb = float(parts[1]) / 1024
                        data.append((name, size_mb))
                    except: pass
            
            # Sort by size desc by default
            data.sort(key=lambda x: x[1], reverse=True)
            
            for name, size in data:
                self.tv_packages.insert('', 'end', values=(name, f"{size:.1f}"))
            
            self.lbl_pkg_count.config(text=f"Total packages: {len(data)}")
        except Exception as e:
            print(f"Pkg error: {e}")
        finally:
            self._set_ui_busy(False)

    def _sort_tv(self, tv, col, reverse, key=lambda x: x):
        l = [(tv.set(k, col), k) for k in tv.get_children('')]
        try:
            l.sort(key=lambda t: key(t[0]), reverse=reverse)
        except ValueError:
             l.sort(reverse=reverse)

        for index, (val, k) in enumerate(l):
            tv.move(k, '', index)

        tv.heading(col, command=lambda: self._sort_tv(tv, col, not reverse, key))

    def _build_debug_tab(self):
        debug_top = ttk.Frame(self.tab_debug)
        debug_top.pack(fill='x', padx=10, pady=10)
        
        ttk.Button(debug_top, text="(Un)Mount TempOS boot medium", command=self.on_toggle_mount).pack(side='left', padx=(0, 10))        
        self.lbl_mount_status = ttk.Label(debug_top, text="Checking...", relief="sunken", padding=(5, 2))
        self.lbl_mount_status.pack(side='left', padx=(0, 10))

        ttk.Button(debug_top, text="Install WiFi Configs", command=self.on_install_wifi).pack(side='left', padx=(0, 10))
        ttk.Button(debug_top, text="Launch stand alone installer", command=self.on_launch_installer).pack(side='left', padx=(0, 10))
        
        self.btn_d2bench = ttk.Button(debug_top, text="Run system benchmark", command=self.on_run_d2bench)
        self.btn_d2bench.pack(side='left', padx=(0, 10))
        
        ttk.Button(debug_top, text="Reboot System", command=self.on_reboot).pack(side='left', padx=(0, 10))

        # Always visible client-filling textbox for debug outputs below all buttons
        self.txt_debug = scrolledtext.ScrolledText(self.tab_debug, wrap=tk.WORD, font=("Consolas", 10))
        self.txt_debug.pack(expand=True, fill='both', padx=10, pady=(0, 10))

        self._update_debug_status()

    def on_launch_installer(self):
        try:
            subprocess.Popen(["/Apps/Scripts/install_tempos"])
        except Exception:
            try:
                subprocess.Popen("/Apps/Scripts/install_tempos", shell=True)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to launch installer: {e}")

    def on_run_d2bench(self):
        self.btn_d2bench.config(state='disabled')
        self.txt_debug.insert(tk.END, "=== Running d2bench ===\n")
        self.txt_debug.see(tk.END)
        
        def run_thread():
            try:
                env = os.environ.copy()
                env["LC_ALL"] = "C"
                proc = subprocess.Popen(
                    "d2bench",
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env
                )
                for line in iter(proc.stdout.readline, ''):
                    self.after(0, self._append_debug_text, line)
                proc.stdout.close()
                proc.wait()
                rc = proc.returncode
                self.after(0, self._append_debug_text, f"\n=== d2bench finished (exit code {rc}) ===\n\n")
            except Exception as e:
                self.after(0, self._append_debug_text, f"\n[ERROR] Failed to run d2bench: {e}\n\n")
            finally:
                self.after(0, lambda: self.btn_d2bench.config(state='normal'))

        threading.Thread(target=run_thread, daemon=True).start()

    def _append_debug_text(self, text):
        self.txt_debug.insert(tk.END, text)
        self.txt_debug.see(tk.END)

    def _build_log_tab(self):
        frm_top = ttk.Frame(self.tab_log)
        frm_top.pack(fill='x', padx=5, pady=5)
        
        ttk.Button(frm_top, text="Refresh", command=self._refresh_log).pack(side='right')
        
        self.txt_log = scrolledtext.ScrolledText(self.tab_log, wrap=tk.WORD, font=("Consolas", 10))
        self.txt_log.pack(expand=True, fill='both', padx=5, pady=5)
        
        self._refresh_log()

    def _refresh_log(self):
        self.txt_log.configure(state='normal')
        self.txt_log.delete('1.0', tk.END)
        
        log_path = os.path.join(self.storage.config_folder, "start_log.txt")
        if os.path.exists(log_path):
            try:
                with open(log_path, 'r') as f:
                    content = f.read()
                self.txt_log.insert(tk.END, content)
            except Exception as e:
                self.txt_log.insert(tk.END, f"Error reading log: {e}")
        else:
            self.txt_log.insert(tk.END, "No startup log found.")
            
        self.txt_log.configure(state='disabled')

    def on_reboot(self):
        self.destroy()
        perform_power_action("reboot")

    def _populate_isos(self):
        self.available_isos = [img['path'] for img in self.storage.images]
        
        options = []
        for img in self.storage.images:
            name = img['filename']
            if img['isCurrent']: name += " (current system)"
            if img['isLatest']: name += " (Latest)"
            options.append(name)
        
        self.cb_install_iso['values'] = options
        if options:
            self.cb_install_iso.current(0)
        else:
            self.cb_install_iso.set('')

    def _refresh_install_destinations(self):
        self.storage.refresh()
        self._populate_isos() # also refresh isos
        for i in self.tv_dest.get_children(): self.tv_dest.delete(i)
        self.install_drives_map = []
        
        for d in self.storage.drives:
            if d.get('TempOs') == 2: continue # Exclude boot partition
            
            # Prepare columns
            device = d['MainDevice'].replace("/dev/", "")
            dtype = "Removable" if d.get('IsRemovable') else "Internal HD"
            
            sz_mb = int(d['Size']) // (1024*1024)
            if sz_mb >= 10240: # > 10GB
                size_str = f"{sz_mb/1024:.1f} GB"
            else:
                size_str = f"{sz_mb} MB"
                
            vol = "TempOS" if d.get('TempOs') == 1 else d['VolumeNames']
            
            self.tv_dest.insert('', 'end', values=(device, dtype, size_str, vol))
            self.install_drives_map.append(d)
        
        self._update_install_ui_state()

    def _refresh_update_partitions(self):
        self.storage.refresh()
        parts = [p for p in self.storage.partitions if p['IsTempOS'] == 1]
        values = [f"{p['PartitionDevice']} ({p['VolumeName']})" for p in parts]
        self.cb_update_part['values'] = values
        if values: self.cb_update_part.current(0)
        else: self.cb_update_part.set('')

    def on_check_online_update(self):
        url = self.ent_update_tab_url.get().strip()
        if not url:
             messagebox.showwarning("Update", "Please enter a URL")
             return
        
        self._set_ui_busy(True)
        self.lbl_update_status.config(text=f"Connecting to {url}...", foreground="blue")
        self.update()

        try:
             self.updates_found = check_online_update(url)
             self._update_updates_dropdown()
        except Exception as e:
            self.lbl_update_status.config(text=f"Check failed: {e}", foreground="red")
            self.lbl_update_target.config(text="Please press [Check for update]", foreground="red")
            self.updates_found = []
            self.update_map = []
            self.cb_update_version.set('')
            self.cb_update_version['values'] = []
            
        self._set_ui_busy(False)

    def on_check_part_update(self):
        part_str = self.cb_update_part.get()
        if not part_str:
            messagebox.showwarning("Update", "Select a partition")
            return
        
        # Extract device path from "device (Label)"
        part_dev = part_str.split()[0] 
        
        self._set_ui_busy(True)
        self.lbl_update_status.config(text=f"Checking {part_dev}...", foreground="blue")
        self.update()

        try:
            self.updates_found = check_partition_update(self.storage, part_dev)
            self._update_updates_dropdown()
        except Exception as e:
            self.lbl_update_status.config(text=f"Check failed: {e}", foreground="red")
            self.lbl_update_target.config(text="Please press [Check for update]", foreground="red")
            self.updates_found = []
            self.update_map = []
            self.cb_update_version.set('')
            self.cb_update_version['values'] = []

        self._set_ui_busy(False)

    def _sort_key(self, s):
        return re.sub(r'(\d+)', lambda m: m.group(1).zfill(10), s)

    def _update_updates_dropdown(self):
        self.updates_found.sort(key=lambda item: self._sort_key(item['name']), reverse=True)
        
        current_name_raw = self.storage.tempos_image
        if current_name_raw:
            # remove .iso for comparison if needed, or assume normalized
            current_name = os.path.splitext(current_name_raw)[0]
        else:
            current_name = ""

        # Normalize key for current to compare rank
        current_key = self._sort_key(current_name)

        # Update Status Label
        status_text = ""
        status_color = "black"
        
        if not self.updates_found:
             status_text = "No updates found"
             status_color = "red"
             self.lbl_update_target.config(text="No updates found", foreground="red")
        else:
            latest_item = self.updates_found[0]
            latest_key = self._sort_key(latest_item['name'])
            
            if latest_key > current_key:
                status_text = "A new version is available"
                status_color = "green"
            elif latest_key == current_key:
                status_text = "The latest version is already installed"
                status_color = "green"
            else:
                status_text = "No update available"
                status_color = "red"
        
        self.lbl_update_status.config(text=status_text, foreground=status_color)

        display_values = []
        self.update_map = [] 
        
        for i, item in enumerate(self.updates_found):
            label = item['name']
            item_key = self._sort_key(item['name'])
            
            is_current = (label == current_name)
            
            # Logic: Top item is ranked above current?
            is_latest = False
            if i == 0:
                if item_key > current_key:
                    is_latest = True
            
            if is_latest: label += " (latest)"
            if is_current: label += " (current)"
            
            is_installed = any(os.path.splitext(img['filename'])[0] == item['name'] for img in self.storage.images)
            if is_installed and not is_current:
                 label += " (installed)"
            
            display_values.append(label)
            self.update_map.append(item)
            
        self.cb_update_version['values'] = display_values
        
        if display_values:
            self.cb_update_version.current(0)
            self._on_update_ver_select(None)
        else:
            self.cb_update_version.set("No updates found")
            self.btn_start_update.config(state='disabled')
            self.update_map = []

    def _on_update_ver_select(self, event):
        idx = self.cb_update_version.current()
        if idx < 0 or idx >= len(self.update_map):
            self.lbl_update_target.config(text="Please press [Check for update]", foreground="black")
            if hasattr(self, 'chk_upd_folders'):
                self.chk_upd_folders.state(['disabled'])
            return
        
        item = self.update_map[idx]
        current_name = os.path.splitext(self.storage.tempos_image)[0] if self.storage.tempos_image else ""
        
        # Sort Keys
        item_key = self._sort_key(item['name'])
        curr_key = self._sort_key(current_name)
        
        is_online = (item.get('type') == 'online')
        mode = "Online" if is_online else "Offline"

        if is_online:
            if hasattr(self, 'chk_upd_folders'):
                self.chk_upd_folders.state(['disabled'])
        else:
            if hasattr(self, 'chk_upd_folders'):
                self.chk_upd_folders.state(['!disabled'])

        if item['name'] == current_name:
            self.btn_start_update.config(state='disabled')
            self.lbl_update_target.config(text="Please select a version", foreground="red")
            self.btn_start_update.config(text="Start Update")
        else:
            self.btn_start_update.config(state='normal')
            
            action = "update"
            if item_key < curr_key:
                action = "downgrade"
                self.btn_start_update.config(text="Start Downgrade")
                self.lbl_update_target.config(text=f"{mode} {action} to {item['name']}", foreground="orange")
            else:
                self.btn_start_update.config(text="Start Update")
                self.lbl_update_target.config(text=f"{mode} {action} to {item['name']}", foreground="green")

    def on_ventoy_update(self):
        dev = self.storage.boot_main_device
        if not dev:
            messagebox.showerror("Error", "Boot device not found.")
            return
            
        dlg = LogDialog(self, "Updating Ventoy")
        try:
             success = False
             for status, msg, replace in run_ventoy_update(dev):
                 dlg.add_line(status, msg, replace)
                 if status == "ERROR":
                     success = False
                     break
                 elif status == "SUCCESS":
                     success = True
                     break
             dlg.finish(success=success)
        except Exception as e:
            dlg.add_line("ERROR", f"Exception: {e}")
            dlg.finish(success=False)

    def on_start_update(self):
        idx = self.cb_update_version.current()
        if idx < 0 or idx >= len(self.update_map): return
        item = self.update_map[idx]
        
        options = {
            'item': item,
            'verify_src': self.var_upd_verify_src.get(),
            'verify_dst': self.var_upd_verify_dst.get(),
            'use_settings': self.var_upd_settings.get(),
            'update_apps': self.var_upd_apps.get(),
            'copy_folders': self.var_upd_folders.get() if item.get('type') == 'local' else False,
            'folders': self.settings.folders,
            'boot_default': self.var_upd_default.get(),
            'cleanup_old': self.var_cleanup_old.get()
        }
        
        dlg = LogDialog(self, "System Update")
        try:
             success = False
             for status, msg, replace in run_update_sequence(self.storage, options):
                 dlg.add_line(status, msg, replace)
                 
                 if status == "ERROR":
                     success = False
                     break
                 elif status == "SUCCESS":
                     success = True
                     break
             # Allow reboot from dialog if successful
             dlg.finish(success=success, show_reboot=True)
             
        except Exception as e:
            dlg.add_line("ERROR", f"Exception: {e}")
            dlg.finish(success=False)

    def _on_dest_select(self, event):
        # Reset Logic
        sel = self.tv_dest.selection()
        if not sel: return
        idx = self.tv_dest.index(sel[0])
        drive = self.install_drives_map[idx]
        
        # Determine Default Mode
        if drive.get('TempOs') == 1:
            self.var_install_mode.set("update")
        else:
            self.var_install_mode.set("erase")
            
        # Reset other defaults
        self.var_update_ventoy.set(True)
        self.var_settings_mode.set("copy")
        self.var_copy_folders.set(False)
        self.var_copy_apps.set(True)
        self.var_boot_default.set(True)
        self.var_verify_src.set(False)
        self.var_verify_dst.set(True)

        self._update_install_ui_state()

    def _update_install_ui_state(self, event=None):
        # 1. ISO source
        if self.var_install_all.get():
            self.cb_install_iso.state(['disabled'])
        else:
            self.cb_install_iso.state(['!disabled'])
            
        # 2. Destination selection
        sel = self.tv_dest.selection()
        if sel:
             idx = self.tv_dest.index(sel[0])
             drive = self.install_drives_map[idx]
        else:
             drive = None
        
        if not drive:
            self.rb_update.state(['disabled'])
            self.rb_erase.state(['disabled'])
            self.btn_install.config(state='disabled')
            return
        
        self.btn_install.config(state='normal')
        self.rb_erase.state(['!disabled'])
        
        # 3. Update vs Erase availability
        is_tempos = (drive.get('TempOs') == 1)
        
        if is_tempos:
            self.rb_update.state(['!disabled'])
        else:
            self.rb_update.state(['disabled'])
            if self.var_install_mode.get() == "update":
                self.var_install_mode.set("erase")
            
        # 4. Mode Logic
        mode = self.var_install_mode.get()
        if mode == "erase":
            self.var_update_ventoy.set(True)
            self.chk_ventoy.state(['disabled'])
            
            self.rb_keep_settings.state(['disabled'])
            if self.var_settings_mode.get() == "keep":
                self.var_settings_mode.set("copy")
        else:
            self.chk_ventoy.state(['!disabled'])
            self.rb_keep_settings.state(['!disabled'])

    def on_start_install(self):
        sel = self.tv_dest.selection()
        if not sel: return
        drive = self.install_drives_map[self.tv_dest.index(sel[0])]
        
        dev = drive['MainDevice']
        size = f"{int(drive['Size']) // (1024*1024)} MB"
        vol = "TempOS" if drive.get('TempOs') == 1 else drive['VolumeNames']
        mode = self.var_install_mode.get()
        
        # 1. Confirmation
        if mode == 'update':
            msg = f"Are you sure you want to update {dev} {size} {vol}? This action can take several minutes and cannot be cancelled."
            if not messagebox.askyesno("Confirm Update", msg, icon='warning'): return
        else:
            # Erase Mode
            if drive['IsRemovable']:
                msg = f"Are you sure you want to install to {dev} {size} {vol}? THIS WILL ERASE ALL DATA!. This action can take several minutes and cannot be cancelled."
            else:
                msg = f"You are NOT installing to a USB stick but to an INTERNAL HARD DRIVE ({dev}). This is fully supported but make sure you really want to do this, overwriting any existing operating system and data. Are you sure to continue?"
            
            if not messagebox.askyesno("Confirm Erase", msg, icon='warning'): return

        # 2. Gather Options
        if self.var_install_all.get():
            isos = self.available_isos
        else:
            idx = self.cb_install_iso.current()
            if idx >= 0 and idx < len(self.available_isos):
                 isos = [self.available_isos[idx]]
            else:
                 isos = []
        
        if not isos:
            messagebox.showerror("Error", "No source image selected/found.")
            return

        options = {
            'drive': drive,
            'iso_list': isos,
            'mode': mode,
            'update_ventoy': self.var_update_ventoy.get(),
            'settings_mode': self.var_settings_mode.get(),
            'copy_folders': self.var_copy_folders.get(),
            'copy_apps': self.var_copy_apps.get(),
            'boot_default': self.var_boot_default.get(),
            'verify_src': self.var_verify_src.get(),
            'verify_dst': self.var_verify_dst.get()
        }

        # 3. Run Blocking Sequence via Dialog
        dlg = LogDialog(self, "Installation")

        try:
            self.storage.refresh()
            success = False
            for status, msg, replace in run_install_sequence(self.storage, self.settings, options):
                dlg.add_line(status, msg, replace)
                if status == "ERROR":
                    success = False
                    break
                elif status == "SUCCESS":
                    success = True
                    break
            
            dlg.finish(success=success)
            self._refresh_install_destinations()
        except Exception as e:
            dlg.add_line("ERROR", f"Critical Error: {e}")
            dlg.finish(success=False)

    def _update_info_text(self):
        self.lbl_ver.config(text=f"{self.storage.TempOs_Brand_Name} version {self.storage.TempOs_Version} ({self.storage.TempOs_Build_Date})")
        
        self.info_text.configure(state='normal')
        self.info_text.delete('1.0', tk.END)
        output = [
            "=== SYSTEM ===", self.storage.get_tempos_summary(),
            "\n=== VERSIONS ===", self.storage.get_versions_summary(),
            "\n=== NETWORK ===", str(self.net),
            "\n=== MEMORY ===", self.mem.get_report(),
            "\n=== STORAGE ===", self.storage.get_summary(),
            "\n=== DRIVES ===", self.storage.get_drive_summary(),
            "\n=== PARTITIONS ===", self.storage.get_partition_summary()
        ]
        self.info_text.insert(tk.END, "\n".join(output))
        self.info_text.configure(state='disabled')

    def on_refresh_info(self):
        self.net = NetInfo()
        self.mem = MemInfo()
        self.storage.refresh()
        self.settings.load()
        self.load_ui_from_settings()
        self._update_info_text()
        self._refresh_install_destinations()
        self._refresh_update_partitions()
        self._refresh_log()
        self._update_debug_status()

    def on_verify_image(self):
        img_path = os.path.join(self.storage.images_location, self.storage.tempos_image)
        dlg = LogDialog(self, "Verifying Image")
        try:
            dlg.add_line("INFO", f"Verifying {self.storage.tempos_image}...")
            success, msg = verify_booted_image(self.storage, img_path)
            dlg.add_line("SUCCESS" if success else "ERROR", msg)
            dlg.finish(success=success)
        except Exception as e:
            dlg.add_line("ERROR", f"Exception: {e}")
            dlg.finish(success=False)

    def _set_ui_busy(self, busy):
        # Simple cursor change for fast operations
        if busy:
            self.config(cursor="watch")
        else:
            self.config(cursor="")

    def _update_debug_status(self):
        mp = self.storage.tempos_mount
        if mp:
            self.lbl_mount_status.config(text=f"Mounted at: {mp}", background="#dff0d8")
            self.btn_save.config(state='normal')
            self.lbl_save_status.config(text="")
        else:
            self.lbl_mount_status.config(text="(not mounted)", background="#f2dede")
            self.btn_save.config(state='disabled')
            self.lbl_save_status.config(text="Mount USB in Debug tab to save settings")

    def on_toggle_mount(self):
        is_mounted_target = os.path.ismount(self.storage.mount_point)
        success, msg = MountTempOsBootMedium(self.storage, unmount=is_mounted_target)
        if not success: messagebox.showerror("Operation Failed", msg)
        else: messagebox.showinfo("Success", f"{'Unmounted' if is_mounted_target else 'Mounted'}: {msg}")
        self.storage.refresh()
        self._update_debug_status()
        self._update_info_text()
        self._refresh_install_destinations()
        self._refresh_update_partitions()

    def on_install_wifi(self):
        msg = add_wifi_adaptors(self.settings)
        messagebox.showinfo("WiFi Install", msg)
        
    def on_reload(self):
        self.settings.load()
        self.load_ui_from_settings()
        messagebox.showinfo("Reload", "Settings reloaded from disk.")

    def on_load_defaults(self):
        path = os.path.join(self.storage.asset_folder, "TempOS.ini.default")
        if os.path.exists(path):
            self.settings.load_from_file(path)
            self.load_ui_from_settings()
            messagebox.showinfo("Defaults", "Default settings loaded (unsaved).")
        else:
            messagebox.showwarning("Error", "Default settings file not found.")

    def _get_tv_items(self, tv, allow_empty_second=False):
        items = []
        for i in tv.get_children():
            vals = tv.item(i)['values']
            if isinstance(vals, (list, tuple)):
                v0 = str(vals[0]).strip() if len(vals) > 0 and vals[0] is not None else ""
                v1 = str(vals[1]).strip() if len(vals) > 1 and vals[1] is not None else ""
            else:
                v0 = str(vals).strip() if vals is not None else ""
                v1 = ""
            # v0 (first column) is always mandatory; v1 is mandatory unless allow_empty_second=True
            if v0 and (v1 or allow_empty_second):
                items.append([v0, v1])
        return items

    def load_ui_from_settings(self):
        self.var_offline.set(self.settings.offline_mode)
        
        self.ent_closestartup.delete(0, tk.END)
        self.ent_closestartup.insert(0, str(self.settings.close_startup_limit) if self.settings.close_startup_limit is not None else "")
        self.ent_updateurl.delete(0, tk.END)
        self.ent_updateurl.insert(0, self.settings.update_url)

        self.var_verify.set(self.settings.verify_on_start)
        self.var_updatecheck.set(self.settings.update_check)
        self.cb_iso.set(self.settings.default_iso)

        if self.settings.kb_layout == 'us-intl':
            self.cb_kblayout.set("QWERTY")
        else:
            self.cb_kblayout.set("AZERTY (System default)")

        for i in self.tv_wifi.get_children(): self.tv_wifi.delete(i)
        for item in self.settings.wifi:
            s = item[0].strip() if len(item) > 0 and item[0] else ""
            p = item[1].strip() if len(item) > 1 and item[1] else ""
            if s:
                self.tv_wifi.insert('', 'end', values=(s, p))
        
        self.ent_home.delete(0, tk.END); self.ent_home.insert(0, self.settings.browser['home'])
        self.ent_start.delete(0, tk.END); self.ent_start.insert(0, self.settings.browser['start'])
        self.var_launch.set(self.settings.browser['launch'])
        
        for i in self.tv_fav.get_children(): self.tv_fav.delete(i)
        for item in self.settings.favorites:
            n = item[0].strip() if len(item) > 0 and item[0] else ""
            u = item[1].strip() if len(item) > 1 and item[1] else ""
            if n and u:
                self.tv_fav.insert('', 'end', values=(n, u))

        for i in self.tv_fold.get_children(): self.tv_fold.delete(i)
        for item in self.settings.folders:
            n = item[0].strip() if len(item) > 0 and item[0] else ""
            m = item[1].strip() if len(item) > 1 and item[1] else ""
            if n and m:
                self.tv_fold.insert('', 'end', values=(n, m))
        
        self.txt_cmd.delete('1.0', tk.END)
        self.txt_cmd.insert('1.0', "\n".join(self.settings.startup_cmds))
        
        # Also update Update tab if it exists (for reload case)
        if hasattr(self, 'ent_update_tab_url'):
             self.ent_update_tab_url.delete(0, tk.END)
             self.ent_update_tab_url.insert(0, self.settings.update_url)

        # Update labels that display mapped folders list
        if hasattr(self, 'chk_copy_folders'):
            self.chk_copy_folders.config(text=f"Copy (overwrite) mapped folders{self._get_mapped_folders_suffix()}")
        if hasattr(self, 'chk_upd_folders'):
            self.chk_upd_folders.config(text=f"Copy mapped folders{self._get_mapped_folders_suffix()}")

    def add_wifi(self):
        s, p = self.ent_ssid.get().strip(), self.ent_pass.get().strip()
        if not s:
            messagebox.showwarning("Input Required", "SSID is mandatory.")
            self.ent_ssid.focus_set()
            return
        self.tv_wifi.insert('', 'end', values=(s, p))
        self.ent_ssid.delete(0, tk.END)
        self.ent_pass.delete(0, tk.END)
        self.ent_ssid.focus_set()

    def add_fav(self):
        n, u = self.ent_fav_n.get().strip(), self.ent_fav_u.get().strip()
        if not n:
            messagebox.showwarning("Input Required", "Favorite Name is mandatory.")
            self.ent_fav_n.focus_set()
            return
        if not u:
            messagebox.showwarning("Input Required", "Favorite URL is mandatory.")
            self.ent_fav_u.focus_set()
            return
        self.tv_fav.insert('', 'end', values=(n, u))
        self.ent_fav_n.delete(0, tk.END)
        self.ent_fav_u.delete(0, tk.END)
        self.ent_fav_n.focus_set()

    def add_fold(self):
        n, m = self.ent_fold_n.get().strip(), self.ent_fold_m.get().strip()
        if not n:
            messagebox.showwarning("Input Required", "Folder name on USB is mandatory.")
            self.ent_fold_n.focus_set()
            return
        if not m:
            messagebox.showwarning("Input Required", "Local mount path is mandatory.")
            self.ent_fold_m.focus_set()
            return
        self.tv_fold.insert('', 'end', values=(n, m))
        self.ent_fold_n.delete(0, tk.END)
        self.ent_fold_m.delete(0, tk.END)
        self.ent_fold_n.focus_set()

    def del_item(self, tv):
        for item in tv.selection(): tv.delete(item)

    def save_settings(self):
        self.settings.offline_mode = self.var_offline.get()
        
        self.settings.close_startup_limit = None
        s_close = self.ent_closestartup.get().strip()
        if s_close:
            try: self.settings.close_startup_limit = int(s_close)
            except: pass

        self.settings.update_url = self.ent_updateurl.get().strip()
        
        self.settings.verify_on_start = self.var_verify.get()
        self.settings.update_check = self.var_updatecheck.get()
        self.settings.default_iso = self.cb_iso.get().strip()
        
        kb_val = self.cb_kblayout.get()
        if kb_val == "QWERTY":
            self.settings.kb_layout = "us-intl"
        else:
            self.settings.kb_layout = "be"

        self.settings.wifi = self._get_tv_items(self.tv_wifi, allow_empty_second=True)
        self.settings.browser['home'] = self.ent_home.get().strip()
        self.settings.browser['start'] = self.ent_start.get().strip()
        self.settings.browser['launch'] = self.var_launch.get()
        self.settings.favorites = self._get_tv_items(self.tv_fav, allow_empty_second=False)
        self.settings.folders = self._get_tv_items(self.tv_fold, allow_empty_second=False)
        self.settings.startup_cmds = self.txt_cmd.get('1.0', tk.END).strip().splitlines()
        
        res = self.settings.save()
        messagebox.showinfo("Result", res)
        self.load_ui_from_settings()