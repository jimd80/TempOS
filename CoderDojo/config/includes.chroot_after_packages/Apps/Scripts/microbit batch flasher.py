#!/usr/bin/env python3 

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import tkinter.font
import os
import shutil
import subprocess
import threading
import time

# --- Configuration ---
DEVICE_LABEL = "MICROBIT"
POLL_INTERVAL_SECONDS = 1
DISCONNECT_POLL_INTERVAL_SECONDS = 0.5

# Status Colors
COLOR_STATUS_ALWAYS_GREEN = "#28a745" 
COLOR_STATUS_DISCONNECT_ORANGE = "#fd7e14"
COLOR_WARNING_RED = "#dc3545"         
COLOR_STATUS_NEUTRAL = "black"       

# --- System Setup ---
try:
    USERNAME = os.getlogin()
except OSError:
    USERNAME = os.environ.get('USER') or os.environ.get('LOGNAME')
    if not USERNAME:
        print("ERROR: Could not determine username.")
        exit(1)

FIXED_MOUNT_POINT_BASE = f"/media/{USERNAME}"
FIXED_MOUNT_POINT = os.path.join(FIXED_MOUNT_POINT_BASE, DEVICE_LABEL)

class MicrobitCopierApp:
    def __init__(self, root_window):
        self.root = root_window
        self.root.title("micro:bit Batch Flasher")
        self.root.configure(background='white') 

        self.style = ttk.Style(self.root)
        available_themes = self.style.theme_names()
        if 'clam' in available_themes: self.style.theme_use('clam')
        elif 'aqua' in available_themes: self.style.theme_use('aqua')
        elif 'vista' in available_themes: self.style.theme_use('vista')
        
        self.default_font_family = 'Helvetica Neue' if 'Helvetica Neue' in tkinter.font.families() else 'Arial'
        self.default_font_size = 10
        self.status_font_size = 12 
        self.default_font_bold = (self.default_font_family, self.default_font_size, 'bold')
        self.default_font_normal = (self.default_font_family, self.default_font_size, 'normal')
        self.status_font_bold = (self.default_font_family, self.status_font_size, 'bold')

        self.style.configure('.', background='white', foreground=COLOR_STATUS_NEUTRAL, font=self.default_font_normal)
        self.style.configure('TLabel', background='white', foreground=COLOR_STATUS_NEUTRAL)
        self.style.configure('TFrame', background='white')
        self.style.configure('TLabelframe.Label', background='white', foreground=COLOR_STATUS_NEUTRAL, font=self.default_font_bold)
        self.style.configure('TEntry', fieldbackground='white', foreground=COLOR_STATUS_NEUTRAL, bordercolor='#cccccc', padding=(5,3))
        
        self.style.configure('Blue.TButton', background='#007bff', foreground='white', font=self.default_font_bold,
                             padding=(10, 5), relief='flat', borderwidth=0)
        self.style.map('Blue.TButton', background=[('active', '#0056b3'), ('pressed', '#004085')],
                       relief=[('pressed', 'sunken'), ('!pressed', 'flat')])
        self.style.configure('Log.TCheckbutton', background='white', foreground=COLOR_STATUS_NEUTRAL)
        
        self.style.configure('MainStatus.TLabel', foreground=COLOR_STATUS_ALWAYS_GREEN, background='white', font=self.status_font_bold)
        self.style.configure('Warning.TLabel', foreground=COLOR_WARNING_RED, background='white', font=self.status_font_bold)
        self.style.configure('Disconnect.TLabel', foreground=COLOR_STATUS_DISCONNECT_ORANGE, background='white', font=self.status_font_bold)
        # Style for an "invisible" warning label (empty text, background color foreground)
        self.style.configure('HiddenWarning.TLabel', foreground='white', background='white', font=self.status_font_bold)


        self.MIN_WINDOW_WIDTH = 550
        self.LOG_AREA_ESTIMATED_HEIGHT = 150

        self.source_filepath = tk.StringVar()
        self.current_status_text = tk.StringVar(value="Status: Idle. Select a .hex file.")
        self.show_log_var = tk.BooleanVar(value=False)

        self.warning_label_text = tk.StringVar() # For warning label text control

        self.worker_thread = None
        self.stop_event = threading.Event()
        self.is_processing_cycle = False
        self.is_watching_state = False
        self.last_processed_device_path = None
        self.logged_waiting_for_file = False
        self.logged_waiting_for_disconnect = False
        self.logged_initial_scan_command = False

        self.content_frame = ttk.Frame(root_window, padding="10 10 10 10", style='TFrame')
        self.content_frame.pack(expand=False, fill="x", side=tk.TOP)

        file_selection_frame = ttk.Frame(self.content_frame, style='TFrame')
        file_selection_frame.pack(fill="x", pady=(0,10), side=tk.TOP)
        ttk.Label(file_selection_frame, text="File to copy:", style='TLabel').pack(side=tk.LEFT, padx=(0,5), pady=5)
        self.filepath_entry = ttk.Entry(file_selection_frame, textvariable=self.source_filepath, width=35, state="readonly", style='TEntry')
        self.filepath_entry.pack(side=tk.LEFT, expand=True, fill="x", padx=5, pady=5)
        self.browse_button = ttk.Button(file_selection_frame, text="Browse .hex...", command=self.browse_file, style='Blue.TButton')
        self.browse_button.pack(side=tk.LEFT, padx=(5,0), pady=5)

        status_warning_frame = ttk.Frame(self.content_frame, style='TFrame')
        status_warning_frame.pack(fill="x", pady=(0,5), side=tk.TOP)
        self.status_label = ttk.Label(status_warning_frame, textvariable=self.current_status_text, anchor="w", style='MainStatus.TLabel')
        self.status_label.pack(fill="x", expand=True, padx=5)
        
        self.warning_label = ttk.Label(status_warning_frame, textvariable=self.warning_label_text, style='Warning.TLabel', anchor="w")
        self.warning_label.pack(fill="x", expand=True, padx=5, pady=(2,0)) # Always packed

        control_frame = ttk.Frame(self.content_frame, style='TFrame')
        control_frame.pack(fill="x", side=tk.TOP, pady=(10,0))
        self.watch_button = ttk.Button(control_frame, text="Start Watching", command=self.toggle_watching, style='Blue.TButton')
        self.watch_button.pack(side=tk.LEFT, padx=5)
        self.log_toggle_button = ttk.Checkbutton(control_frame, text="Show Detailed Log",
                                                 variable=self.show_log_var, command=self.toggle_log_visibility, style='Log.TCheckbutton')
        self.log_toggle_button.pack(side=tk.LEFT, padx=10)
        self.exit_button = ttk.Button(control_frame, text="Exit", command=self.exit_app, style='Blue.TButton')
        self.exit_button.pack(side=tk.RIGHT, padx=5)

        self.detailed_log_text = scrolledtext.ScrolledText(root_window, wrap=tk.WORD, height=7, state="disabled", relief="sunken", borderwidth=1,
                                                          background="#f8f9fa", foreground=COLOR_STATUS_NEUTRAL, font=('Menlo', 9) if 'Menlo' in tkinter.font.families() else ('Consolas', 9, 'normal'),
                                                          padx=5, pady=5)
        
        self.root.protocol("WM_DELETE_WINDOW", self.exit_app)
        self._update_status("Idle. Select a .hex file.")
        self._update_warning_label(show=False) # Set initial state (empty text, hidden style)
        self._log_detail(f"Flasher initialized. User: {USERNAME}, Mount target: {FIXED_MOUNT_POINT}", "INFO")
        
        self.root.update_idletasks() 
        content_actual_height = self.content_frame.winfo_reqheight()
        decorations_height = self.root.winfo_rooty() - self.root.winfo_y()
        if decorations_height < 0: decorations_height = 30 # Handle potential negative if called too early

        self.initial_height_no_log = content_actual_height + decorations_height + 10

        content_actual_width = self.content_frame.winfo_reqwidth()
        self.initial_width = content_actual_width + 20 
        self.initial_width = max(self.MIN_WINDOW_WIDTH, self.initial_width)

        self.root.minsize(self.MIN_WINDOW_WIDTH, self.initial_height_no_log) 
        self.root.geometry(f"{self.initial_width}x{self.initial_height_no_log}")
        
        self.show_log_var.set(False)
        self.toggle_log_visibility()


    def _update_status(self, message, style_or_color='MainStatus.TLabel'): # Same as before
        if hasattr(self, 'root') and self.root.winfo_exists():
            def do_update():
                if self.status_label.winfo_exists():
                    self.current_status_text.set(f"Status: {message}")
                    if style_or_color.startswith('#') or \
                       style_or_color in [COLOR_STATUS_ALWAYS_GREEN, COLOR_STATUS_DISCONNECT_ORANGE, COLOR_WARNING_RED, COLOR_STATUS_NEUTRAL, 'red', 'green', 'orange', 'black']:
                        color_to_use = style_or_color
                        if color_to_use == COLOR_STATUS_ALWAYS_GREEN: final_style = 'MainStatus.TLabel'
                        elif color_to_use == COLOR_STATUS_DISCONNECT_ORANGE: final_style = 'Disconnect.TLabel'
                        elif color_to_use == COLOR_WARNING_RED: final_style = 'Warning.TLabel'
                        else: 
                            final_style = f"{color_to_use.capitalize()}Direct.TLabel"
                            try: self.style.configure(final_style, foreground=color_to_use, background='white', font=self.status_font_bold)
                            except tk.TclError: 
                                try: self.status_label.config(foreground=color_to_use, background='white', font=self.status_font_bold); return
                                except tk.TclError: pass; return 
                        try: self.status_label.config(style=final_style)
                        except tk.TclError: pass
                    else: 
                        try: self.status_label.config(style=style_or_color)
                        except tk.TclError: pass
            self.root.after(0, do_update)

    def _update_warning_label(self, show=False): # Corrected
        if hasattr(self, 'root') and self.root.winfo_exists():
            def do_update():
                if self.warning_label.winfo_exists():
                    if show:
                        self.warning_label_text.set("DO NOT DISCONNECT micro:bit!")
                        try: self.warning_label.config(style='Warning.TLabel')
                        except tk.TclError: self.warning_label.config(foreground=COLOR_WARNING_RED) # Fallback
                    else:
                        self.warning_label_text.set("") # Empty text
                        # Apply a style that makes it "invisible" or just rely on empty text
                        try: self.warning_label.config(style='HiddenWarning.TLabel') # Assumes HiddenWarning.TLabel is configured
                        except tk.TclError: self.warning_label.config(foreground='white') # Fallback: match background
            self.root.after(0, do_update)


    def _log_detail(self, message, level="INFO"): # Same as before
        if hasattr(self, 'root') and self.root.winfo_exists():
            def append_log():
                if self.detailed_log_text.winfo_exists():
                    self.detailed_log_text.configure(state="normal")
                    self.detailed_log_text.insert(tk.END, f"[{level}] {message}\n")
                    self.detailed_log_text.configure(state="disabled")
                    self.detailed_log_text.see(tk.END)
            self.root.after(0, append_log)

    def toggle_log_visibility(self): # Corrected
        self.root.update_idletasks() 
        current_width = self.root.winfo_width() 

        if self.show_log_var.get():
            self.detailed_log_text.pack(fill="both", expand=True, side=tk.TOP, padx=10, pady=(5,10)) 
            self.root.update_idletasks() 
            
            log_actual_height = self.detailed_log_text.winfo_reqheight()
            if log_actual_height < 50: log_actual_height = self.LOG_AREA_ESTIMATED_HEIGHT 

            new_height_with_log = self.initial_height_no_log + log_actual_height + 15 
            
            self.root.geometry(f"{current_width}x{new_height_with_log}")
        else:
            self.detailed_log_text.pack_forget()
            final_height = self.initial_height_no_log
            self.root.geometry(f"{current_width}x{final_height}")


    def browse_file(self): # Updated
        if self.is_processing_cycle: 
            self._log_detail("Cannot select new file during an active device operation. Please wait.", "WARN")
            self._update_status("Busy with device, cannot change file now.")
            return
            
        self._log_detail("Browse button clicked. Opening file dialog...", "INFO")
        filepath = filedialog.askopenfilename(title="Select .hex file for micro:bit",
                                            filetypes=(("Hex files", "*.hex"), ("All files", "*.*")))
        if filepath:
            self.source_filepath.set(filepath)
            self._log_detail(f"File selected via dialog: {filepath}", "INFO")
            self._update_status(f"File selected. Ready to watch.")
            if not self.is_watching_state: 
                self.start_watching()
        else: self._log_detail("File dialog cancelled.", "INFO")

    def toggle_watching(self): # Updated
        if self.is_watching_state:
            self.stop_watching()
        else:
            self.start_watching()

    def start_watching(self): # Updated browse button logic
        self._log_detail("Start Watching button clicked or auto-started.", "INFO")
        if not self.source_filepath.get():
            self._log_detail("Start watching failed: No file selected.", "WARN")
            self._update_status("No file selected."); return
        if not os.path.exists(self.source_filepath.get()):
            self._log_detail(f"Start watching failed: Source file '{self.source_filepath.get()}' not found.", "ERROR")
            self._update_status("Source file not found."); return
        
        if self.worker_thread and self.worker_thread.is_alive(): 
            self._log_detail("Watcher is already running.", "INFO")
            return

        self._log_detail("Verifying base mount directory structure...", "INFO")
        if not os.path.isdir(FIXED_MOUNT_POINT_BASE):
            self._log_detail(f"Base mount directory '{FIXED_MOUNT_POINT_BASE}' missing. Attempting sudo creation...", "INFO")
            try:
                cmd_mkdir = ['sudo', 'mkdir', '-p', FIXED_MOUNT_POINT_BASE]; self._log_detail(f"Executing: {' '.join(cmd_mkdir)}", "CMD"); subprocess.run(cmd_mkdir, check=True, timeout=10)
                cmd_chown = ['sudo', 'chown', f'{USERNAME}:{USERNAME}', FIXED_MOUNT_POINT_BASE]; self._log_detail(f"Executing: {' '.join(cmd_chown)}", "CMD"); subprocess.run(cmd_chown, check=True, timeout=10)
                self._log_detail(f"Base directory '{FIXED_MOUNT_POINT_BASE}' created and ownership set.", "INFO")
            except Exception as e:
                self._log_detail(f"FATAL: Failed to create/own base mount directory '{FIXED_MOUNT_POINT_BASE}': {e}", "ERROR")
                self._update_status("Error creating base mount dir."); self._update_warning_label(True); return
        else: self._log_detail(f"Base mount directory '{FIXED_MOUNT_POINT_BASE}' already exists.", "INFO")
        
        self.stop_event.clear(); self.logged_waiting_for_file = False; self.logged_waiting_for_disconnect = False; self.logged_initial_scan_command = False
        self.worker_thread = threading.Thread(target=self._processing_loop, daemon=True)
        self._log_detail(f"Starting worker thread for device watching (Label: '{DEVICE_LABEL}').", "INFO"); 
        
        self.is_watching_state = True 
        self.watch_button.config(text="Stop Watching")
        # Browse button state managed in _processing_loop 
        
        self.worker_thread.start()

    def stop_watching(self): # Updated
        self._log_detail("Stop Watching button clicked.", "INFO")
        if self.worker_thread and self.worker_thread.is_alive():
            self._update_status("Stopping watcher..."); self._update_warning_label(False)
            self._log_detail("Signalling worker thread to stop.", "INFO"); self.stop_event.set()
        else: 
            self._set_idle_ui_state()


    def _set_idle_ui_state(self): 
        """Resets UI elements to their idle state."""
        self.is_watching_state = False
        if hasattr(self, 'watch_button') and self.watch_button.winfo_exists(): # Check if button exists
            self.watch_button.config(text="Start Watching")
        if hasattr(self, 'browse_button') and self.browse_button.winfo_exists():
            self.browse_button.config(state="normal") 
        self._update_status("Idle. Select a .hex file.")
        self._update_warning_label(False)

    def _processing_loop(self):
        self.is_processing_cycle = False; self._log_detail("Worker thread started. Main processing loop initiated.", "INFO")
        # Initial state of browse button when loop starts (it's polling, not yet processing a device)
        self.root.after(0, lambda: self.browse_button.config(state="normal") if hasattr(self, 'browse_button') and self.browse_button.winfo_exists() else None)


        while not self.stop_event.is_set():
            if self.is_processing_cycle: 
                self.root.after(0, lambda: self.browse_button.config(state="disabled") if hasattr(self, 'browse_button') and self.browse_button.winfo_exists() else None)
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            
            self.root.after(0, lambda: self.browse_button.config(state="normal") if hasattr(self, 'browse_button') and self.browse_button.winfo_exists() else None)


            current_source_file = self.source_filepath.get()
            if not current_source_file or not os.path.exists(current_source_file):
                if not self.logged_waiting_for_file: self._log_detail("Waiting for a valid source file to be selected.", "INFO"); self.logged_waiting_for_file = True
                self._update_status("Waiting for valid source file..."); self._update_warning_label(False); time.sleep(POLL_INTERVAL_SECONDS * 2); continue
            if self.logged_waiting_for_file: self.logged_waiting_for_file = False
            if self.last_processed_device_path:
                if not self.logged_waiting_for_disconnect: self._log_detail(f"Awaiting physical disconnect of {self.last_processed_device_path}...", "INFO"); self.logged_waiting_for_disconnect = True
                if not self._wait_for_device_disconnect_thread(self.last_processed_device_path):
                    if self.stop_event.is_set(): break
                    self._log_detail(f"Warning: Did not confirm clean disconnect of {self.last_processed_device_path}. Proceeding carefully.", "WARN")
                self.last_processed_device_path = None; self.logged_waiting_for_disconnect = False; self.logged_initial_scan_command = False
                self._update_status("Waiting for a micro:bit to connect..."); self._update_warning_label(False)
            if not self.last_processed_device_path:
                if not self.logged_initial_scan_command:
                    lsblk_cmd_str = ' '.join(['lsblk', '-o', 'LABEL,NAME,FSTYPE', '-npl'])
                    self._log_detail(f"Scanning for device with label '{DEVICE_LABEL}' (using: {lsblk_cmd_str})...", "INFO"); self.logged_initial_scan_command = True
                self._update_status("Waiting for a micro:bit to connect..."); self._update_warning_label(False)
                device_path, fstype = self._find_microbit_device_path_thread()
            else: device_path, fstype = None, None
            if device_path and not self.stop_event.is_set():
                self.root.after(0, lambda: self.browse_button.config(state="disabled") if hasattr(self, 'browse_button') and self.browse_button.winfo_exists() else None)
                self.logged_initial_scan_command = False; self.is_processing_cycle = True; self.last_processed_device_path = device_path
                self._log_detail(f"Device candidate found: {device_path} (Type: {fstype}, Label: {DEVICE_LABEL}). Starting processing cycle.", "INFO")
                self._update_status(f"micro:bit ({os.path.basename(device_path)}) detected."); self._update_warning_label(True)
                if self._mount_device_thread(device_path, fstype):
                    if self._copy_file_to_device_thread(current_source_file): self._sync_and_unmount_thread()
                    else: self._log_detail(f"Copy operation failed for {device_path}.", "ERROR"); self._update_status("Copy failed. Cleaning up..."); self._update_warning_label(True); self._cleanup_fixed_mount_point_thread(force_unmount_if_needed=True); self.last_processed_device_path = None
                else: self._log_detail(f"Mount operation failed for {device_path}.", "ERROR"); self._update_status("Mount failed. Will retry."); self._update_warning_label(True); self.last_processed_device_path = None
                self.is_processing_cycle = False
            if self.stop_event.is_set(): break
            time.sleep(POLL_INTERVAL_SECONDS)
        if self.is_processing_cycle and self.last_processed_device_path: self._log_detail("Worker thread stopping mid-cycle. Attempting final cleanup...", "WARN"); self._cleanup_fixed_mount_point_thread(force_unmount_if_needed=True)
        self.root.after(0, self._set_idle_ui_state)
        self._log_detail("Worker thread processing loop has ended.", "INFO")

    def _find_microbit_device_path_thread(self): # Corrected
        try:
            lsblk_cmd = ['lsblk', '-o', 'LABEL,NAME,FSTYPE', '-npl']
            result = subprocess.run(lsblk_cmd, capture_output=True, text=True, check=False, timeout=3)
            if result.returncode == 0:
                for line_idx, line in enumerate(result.stdout.strip().split('\n')):
                    if not line.strip(): continue
                    parts = line.split() 
                    if not parts: continue
                    current_label = parts[0]
                    if current_label == DEVICE_LABEL:
                        device_node = parts[1] if len(parts) > 1 else None
                        fstype = parts[2] if len(parts) > 2 else "vfat"
                        if device_node and device_node.startswith('/dev/'):
                            return device_node, (fstype if "fat" in fstype.lower() else "vfat")
        except subprocess.TimeoutExpired: pass
        except Exception as e: self._log_detail(f"Error in _find_microbit_device_path_thread: {e}", "ERROR")
        return None, None

    def _mount_device_thread(self, device_path, fstype): # 그대로
        self._log_detail(f"Attempting to mount device {device_path}...", "INFO"); self._cleanup_fixed_mount_point_thread(force_unmount_if_needed=True)
        self._update_status(f"Mounting {os.path.basename(device_path)}..."); self._update_warning_label(True)
        if not os.path.isdir(FIXED_MOUNT_POINT):
            self._log_detail(f"Mount point directory '{FIXED_MOUNT_POINT}' missing. Attempting creation...", "INFO")
            try: os.makedirs(FIXED_MOUNT_POINT, exist_ok=True); self._log_detail(f"Directory '{FIXED_MOUNT_POINT}' created by script.", "INFO")
            except OSError:
                self._log_detail(f"Failed to create '{FIXED_MOUNT_POINT}' as user. Attempting with sudo...", "WARN")
                try:
                    cmd_mkdir = ['sudo', 'mkdir', '-p', FIXED_MOUNT_POINT]; self._log_detail(f"Executing: {' '.join(cmd_mkdir)}", "CMD"); subprocess.run(cmd_mkdir, check=True, timeout=5)
                    cmd_chown = ['sudo', 'chown', f'{USERNAME}:{USERNAME}', FIXED_MOUNT_POINT]; self._log_detail(f"Executing: {' '.join(cmd_chown)}", "CMD"); subprocess.run(cmd_chown, check=True, timeout=5)
                    self._log_detail(f"Directory '{FIXED_MOUNT_POINT}' created and owned via sudo.", "INFO")
                except Exception as mkdir_e: self._log_detail(f"FATAL: Failed to create mount point '{FIXED_MOUNT_POINT}': {mkdir_e}", "ERROR"); self._update_status(f"Error creating {FIXED_MOUNT_POINT}"); self._update_warning_label(True); return False
        try:
            import pwd; user_info = pwd.getpwnam(USERNAME); uid, gid = user_info.pw_uid, user_info.pw_gid
            mount_options = f"uid={uid},gid={gid},utf8,flush,rw"
        except Exception: self._log_detail("Could not get UID/GID for mount, using defaults.", "WARN"); mount_options = "defaults,rw,flush"
        mount_cmd = ['sudo', 'mount', '-t', fstype, '-o', mount_options, device_path, FIXED_MOUNT_POINT]
        self._log_detail(f"Executing: {' '.join(mount_cmd)}", "CMD"); mount_proc = subprocess.run(mount_cmd, capture_output=True, text=True, timeout=15)
        if self.stop_event.is_set(): self._log_detail("Mount cancelled by stop event.", "WARN"); self._update_warning_label(False); return False
        if mount_proc.returncode == 0:
            self._log_detail(f"Successfully mounted {device_path} at {FIXED_MOUNT_POINT}.", "INFO"); self._update_status(f"{os.path.basename(device_path)} mounted."); self._update_warning_label(True); return True
        else:
            err_msg = mount_proc.stderr.strip() or mount_proc.stdout.strip() or "Unknown mount error"
            self._log_detail(f"Mount command failed for {device_path}. RC={mount_proc.returncode}. Error: {err_msg}", "ERROR"); self._update_status(f"Mount failed: {os.path.basename(device_path)}"); self._update_warning_label(True); return False

    def _copy_file_to_device_thread(self, source_file_path): # 그대로
        target_filename = os.path.basename(source_file_path)
        destination_path = os.path.join(FIXED_MOUNT_POINT, target_filename)
        self._log_detail(f"Starting direct copy of '{source_file_path}' to '{destination_path}'...", "INFO")
        self._update_status(f"Copying {target_filename} (please wait)..."); self._update_warning_label(True)
        try:
            with open(source_file_path, 'rb') as fsrc, open(destination_path, 'wb') as fdst: shutil.copyfileobj(fsrc, fdst)
            if self.stop_event.is_set():
                self._log_detail("Copy operation potentially interrupted by stop event.", "WARN"); self._update_status("Copy cancelled."); self._update_warning_label(False); return False
            self._log_detail(f"File copy completed using shutil.copyfileobj.", "INFO")
            return True
        except Exception as e:
            self._log_detail(f"Error during direct file copy: {e}", "ERROR"); self._update_status(f"Error copying: {e}"); self._update_warning_label(True); return False

    def _sync_and_unmount_thread(self): # 그대로
        self._update_status(f"Syncing {DEVICE_LABEL} filesystem..."); self._update_warning_label(True)
        self._log_detail("Starting filesystem sync operation...", "INFO")
        try:
            cmd_sync_specific = ['sync', FIXED_MOUNT_POINT]; self._log_detail(f"Executing: {' '.join(cmd_sync_specific)}", "CMD")
            sync_proc = subprocess.run(cmd_sync_specific, check=False, timeout=30, capture_output=True, text=True)
            if sync_proc.returncode != 0:
                self._log_detail(f"Specific sync failed (rc={sync_proc.returncode}): {sync_proc.stderr.strip()}. Trying global sync.", "WARN")
                cmd_sync_global = ['sync']; self._log_detail(f"Executing: {' '.join(cmd_sync_global)}", "CMD"); subprocess.run(cmd_sync_global, check=True, timeout=30)
            self._log_detail("Filesystem sync complete.", "INFO"); self._update_status(f"{DEVICE_LABEL} synced. Unmounting..."); self._update_warning_label(True)
        except Exception as e: self._log_detail(f"Exception during sync operation: {e}", "ERROR"); self._update_status(f"Sync error: {e}"); self._update_warning_label(True)
        if self.stop_event.is_set():
            self._log_detail("Stop event during sync/unmount. Attempting cleanup.", "WARN"); self._cleanup_fixed_mount_point_thread(force_unmount_if_needed=True)
            self.last_processed_device_path = None; self._update_warning_label(False); return
        unmounted_successfully = self._cleanup_fixed_mount_point_thread(force_unmount_if_needed=True)
        if not unmounted_successfully: self.last_processed_device_path = None; self._update_status(f"{DEVICE_LABEL} processed (unmount error). Ready for next."); self._update_warning_label(True)

    def _cleanup_fixed_mount_point_thread(self, force_unmount_if_needed=False): # 그대로
        self._log_detail(f"Cleanup check for mount point '{FIXED_MOUNT_POINT}'. Force: {force_unmount_if_needed}", "INFO")
        is_mounted = False
        try:
            if os.path.exists(FIXED_MOUNT_POINT):
                cmd_mountpoint = ['mountpoint', '-q', FIXED_MOUNT_POINT]
                if subprocess.run(cmd_mountpoint, timeout=1).returncode == 0: is_mounted = True; self._log_detail(f"'{FIXED_MOUNT_POINT}' is currently mounted.", "INFO")
        except Exception as e: self._log_detail(f"Error checking mountpoint status for '{FIXED_MOUNT_POINT}': {e}", "WARN")
        if is_mounted or force_unmount_if_needed:
            if not is_mounted and force_unmount_if_needed: self._log_detail(f"Cleanup: Forcing unmount for '{FIXED_MOUNT_POINT}' (though it was reported as not mounted).", "INFO")
            self._log_detail(f"Attempting to unmount '{FIXED_MOUNT_POINT}'...", "INFO")
            umount_cmd = ['sudo', 'umount', FIXED_MOUNT_POINT]; self._log_detail(f"Executing: {' '.join(umount_cmd)}", "CMD")
            umount_proc = subprocess.run(umount_cmd, capture_output=True, text=True, timeout=10)
            if self.stop_event.is_set(): self._log_detail("Unmount cancelled by stop event.", "WARN"); return False
            if umount_proc.returncode == 0: self._log_detail(f"Successfully unmounted '{FIXED_MOUNT_POINT}'.", "INFO"); return True
            else:
                err_msg = umount_proc.stderr.strip() or umount_proc.stdout.strip()
                if err_msg and "not mounted" not in err_msg.lower() and "mountpoint not found" not in err_msg.lower():
                    self._log_detail(f"Unmount command failed for '{FIXED_MOUNT_POINT}'. RC={umount_proc.returncode}. Error: {err_msg}", "ERROR"); return False
                else: self._log_detail(f"Unmount check: '{FIXED_MOUNT_POINT}' was already unmounted or not a mountpoint (message: {err_msg}).", "INFO"); return True
        else: self._log_detail(f"Cleanup: '{FIXED_MOUNT_POINT}' not mounted, no unmount action needed.", "INFO"); return True

    def _wait_for_device_disconnect_thread(self, device_path_to_check): # Uses COLOR_STATUS_DISCONNECT_ORANGE
        self._update_status(f"Please disconnect micro:bit ({os.path.basename(device_path_to_check)})...", COLOR_STATUS_DISCONNECT_ORANGE); self._update_warning_label(False)
        while not self.stop_event.is_set():
            try:
                lsblk_cmd = ['lsblk', '-o', 'NAME', '-npl']
                result = subprocess.run(lsblk_cmd, capture_output=True, text=True, check=False, timeout=2)
                if result.returncode == 0:
                    if device_path_to_check not in result.stdout:
                        self._log_detail(f"Device {device_path_to_check} confirmed disconnected from system.", "INFO")
                        self._update_status("Device disconnected. Ready for next."); self._update_warning_label(False); return True
            except subprocess.TimeoutExpired: pass
            except Exception as e: self._log_detail(f"Exception checking disconnect for {device_path_to_check}: {e}. Retrying.", "WARN")
            time.sleep(DISCONNECT_POLL_INTERVAL_SECONDS)
        self._log_detail(f"Stopped waiting for disconnect of {device_path_to_check} (stop event).", "WARN"); self._update_warning_label(False); return False

    def exit_app(self): # 그대로
        self._log_detail("Exit button clicked. Initiating shutdown sequence...", "INFO")
        self._update_status("Exiting... (please wait)"); self._update_warning_label(False)
        self.root.update_idletasks() 
        thread_to_join = None
        if self.worker_thread and self.worker_thread.is_alive():
            self._log_detail("Signalling worker thread to stop for exit...", "INFO"); self.stop_event.set()
            thread_to_join = self.worker_thread
        self.root.after(150, self._perform_exit_destroy, thread_to_join)

    def _perform_exit_destroy(self, thread_to_join): # 그대로
        if thread_to_join and thread_to_join.is_alive():
            self._log_detail("Waiting for worker thread to join (max 3s)...", "INFO")
            thread_to_join.join(timeout=3)
            if thread_to_join.is_alive(): self._log_detail("Worker thread did not join in time. Proceeding with exit.", "WARN")
            else: self._log_detail("Worker thread joined successfully.", "INFO")
        try:
            if os.path.exists(FIXED_MOUNT_POINT) and os.path.ismount(FIXED_MOUNT_POINT):
                 self._log_detail(f"Exit: '{FIXED_MOUNT_POINT}' still mounted. Attempting final lazy unmount...", "WARN")
                 cmd_umount_lazy = ['sudo', 'umount', '-l', FIXED_MOUNT_POINT]
                 self._log_detail(f"Executing: {' '.join(cmd_umount_lazy)}", "CMD"); subprocess.run(cmd_umount_lazy, timeout=5, check=False)
        except Exception as e: self._log_detail(f"Exception during final unmount attempt: {e}", "ERROR")
        self._log_detail("Destroying main window. Application will now close.", "INFO")
        if hasattr(self, 'root') and self.root.winfo_exists(): self.root.destroy()

if __name__ == "__main__":
    if not USERNAME: print("CRITICAL ERROR: Username could not be determined. Exiting."); exit(1)
    root = tk.Tk()
    app = MicrobitCopierApp(root)
    root.mainloop()