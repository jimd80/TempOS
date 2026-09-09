#!/usr/bin/python3
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import requests
import threading
import time
import os
import shutil
import json
from datetime import datetime
from urllib.parse import urljoin
import re

# Enum Maps matching API
PARTICIPATION_STATUS_MAP = {
    0: 'NoParticipation', 
    1: 'Participate', 
    2: 'Cancelled'
}

PRESENTATION_TYPE_MAP = {
    0: 'Scratch', 
    1: 'Micro:bit', 
    2: 'AI', 
    3: 'Website',
    4: 'TurboWarp',
    5: 'Other'
}

PRESENTATION_POSITION_MAP = {
    0: 'DontCare',
    1: 'First',
    2: 'Begin',
    3: 'Mid',
    4: 'End',
    5: 'Final'
}

class SyncApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Show & Tell Sync Client")
        self.root.geometry("550x500")
        self.root.resizable(False, True)

        self.is_syncing = False
        self.sync_thread = None
        self.session = None
        self.last_change_timestamp = 0

        self.setup_gui()

    def setup_gui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.columnconfigure(1, weight=1)

        # URL Input
        ttk.Label(main_frame, text="URL:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.url_var = tk.StringVar(value="https://justapp.be/")
        ttk.Entry(main_frame, textvariable=self.url_var).grid(row=0, column=1, sticky=tk.EW, padx=5)

        # Admin Login
        ttk.Label(main_frame, text="Admin Login:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.admin_login_var = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.admin_login_var, show="*").grid(row=1, column=1, sticky=tk.EW, padx=5)

        # Sync Folder
        ttk.Label(main_frame, text="Sync to Folder:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.folder_var = tk.StringVar(value="/home/user/Documenten/show")
        folder_entry = ttk.Entry(main_frame, textvariable=self.folder_var, state="readonly")
        folder_entry.grid(row=2, column=1, sticky=tk.EW, padx=5)
        self.browse_button = ttk.Button(main_frame, text="Browse...", command=self.browse_folder)
        self.browse_button.grid(row=2, column=2, sticky=tk.E)

        # Start/Stop Button
        self.sync_button = ttk.Button(main_frame, text="Start Sync", command=self.toggle_sync)
        self.sync_button.grid(row=3, column=0, columnspan=3, pady=10, sticky=tk.EW)

        # Participants List
        list_frame = ttk.Frame(main_frame)
        list_frame.grid(row=4, column=0, columnspan=3, sticky=tk.NSEW, padx=5, pady=5)
        
        v_scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        h_scrollbar = ttk.Scrollbar(list_frame, orient=tk.HORIZONTAL)
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.participants_text = tk.Text(list_frame, height=10, width=40, state=tk.DISABLED, 
                                         yscrollcommand=v_scrollbar.set, 
                                         xscrollcommand=h_scrollbar.set,
                                         wrap=tk.NONE)
        self.participants_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        v_scrollbar.config(command=self.participants_text.yview)
        h_scrollbar.config(command=self.participants_text.xview)

        main_frame.rowconfigure(4, weight=1)

        # Status Bar
        status_frame = ttk.Frame(self.root, relief=tk.SUNKEN, padding="2 5")
        status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Status: Stopped")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor=tk.W)
        self.status_label.pack(fill=tk.X)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def browse_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.folder_var.set(folder_selected)

    def toggle_sync(self):
        if self.is_syncing:
            self.update_status("Status: Stopping sync...")
            self.is_syncing = False
            self.sync_button.config(state=tk.DISABLED)
        else:
            if not self.url_var.get() or not self.admin_login_var.get() or not self.folder_var.get():
                messagebox.showerror("Error", "Please fill in all fields before starting.")
                return

            self.is_syncing = True
            self.sync_button.config(text="Stop Sync")
            self.browse_button.config(state=tk.DISABLED)

            self.sync_thread = threading.Thread(target=self.sync_worker, daemon=True)
            self.sync_thread.start()

    def update_status(self, message):
        self.root.after(0, lambda: self.status_var.set(message))

    def _api_post(self, payload):
        try:
            url = self.url_var.get().strip()
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            if not url.endswith('/'):
                url += '/'

            response = self.session.post(url, json=payload, timeout=10)
            response.raise_for_status()

            # Fix for HTTP/HTTPS cookie issues:
            # If server sets Secure cookies but client is HTTP, requests won't send them back.
            # Manually copying name/value resets the flags to default (non-secure) allowing HTTP transmission.
            for cookie in response.cookies:
                self.session.cookies.set(cookie.name, cookie.value)

            data = response.json()
            if data.get("Error"):
                raise Exception(data["Error"].get("UserMessage", "Unknown API error"))

            return data.get("Result")

        except requests.exceptions.RequestException as e:
            raise Exception(f"Network error: {e}")
        except json.JSONDecodeError:
            raise Exception("Invalid response from server (not JSON).")
        except Exception as e:
            raise e

    def sync_worker(self):
        self.session = requests.Session()
        self.last_change_timestamp = 0
        admin_code = self.admin_login_var.get()

        try:
            # 1. Start Session
            self.update_status("Status: Starting session...")
            self._api_post({"Method": "StartSession", "LogText": "SyncClient Start"})

            # 2. Login (using ShowLoginAndStatus)
            self.update_status("Status: Logging in...")
            login_result = self._api_post({"Method": "ShowLoginAndStatus", "LoginCode": admin_code})

            if not login_result or not login_result.get("IsAdmin"):
                raise Exception("Login failed or user is not an admin.")
            
            # 3. Initial Sync
            self.update_status("Status: Syncing...")
            self.last_change_timestamp = login_result.get("LastChange", 0)
            self.write_participants_file(login_result.get("Participants", []))
            self.update_participants_ui(login_result.get("Participants", []))
            self.sync_presentation_files(login_result)
            
            # 4. Polling Loop
            while self.is_syncing:
                try:
                    status_result = self._api_post({"Method": "ShowLoginAndStatus", "LoginCode": admin_code})
                    if status_result:
                        current_change = status_result.get("LastChange", 0)
                        if current_change != self.last_change_timestamp:
                            self.update_status("Status: Change detected, updating...")
                            self.write_participants_file(status_result.get("Participants", []))
                            self.update_participants_ui(status_result.get("Participants", []))
                            self.sync_presentation_files(status_result)
                            self.last_change_timestamp = current_change
                        
                        # Format "Synced" time (LastChange from Server)
                        if self.last_change_timestamp:
                            dt_synced = datetime.fromtimestamp(self.last_change_timestamp / 1000.0)
                            synced_time = dt_synced.strftime('%H:%M:%S')
                        else:
                            synced_time = "--:--:--"

                        # Format "Last checked" time (Local client time)
                        checked_time = time.strftime('%H:%M:%S')

                        self.update_status(f"Status: Synced {synced_time} (Last checked: {checked_time})")
                except Exception as e:
                    self.update_status(f"Status: Error during poll: {e}")

                # Sleep with check
                for _ in range(5):
                    if not self.is_syncing: break
                    time.sleep(1)

        except Exception as e:
            self.update_status(f"Status: ERROR - {e}")
            messagebox.showerror("Error", str(e))
        finally:
            self.is_syncing = False
            self.root.after(0, self.reset_ui_to_stopped)

    def write_participants_file(self, participants):
        folder = self.folder_var.get()
        filepath = os.path.join(folder, "participants.txt")

        # Sorting Logic:
        # Group 1: Participating AND AssignedPosition != -1 (Sorted by AssignedPosition)
        # Group 2: Others (Sorted by LoginCode)
        def sort_key(p):
            status = p.get('ParticipationStatus', 0)
            pos = p.get('AssignedPosition', -1)
            if pos is None: pos = -1
            login = p.get('LoginCode', '') or ''
            
            # If active participating and valid position -> Group 0, else Group 1
            if status == 1 and pos >= 0:
                return (0, pos, login)
            return (1, 0, login)

        sorted_participants = sorted(participants, key=sort_key)

        try:
            print("[WRITE] Updating participants.txt")
            with open(filepath, 'w', encoding='utf-8') as f:
                for p in sorted_participants:
                    # Determine prefix (01 or --)
                    status = p.get('ParticipationStatus', 0)
                    pos_val = p.get('AssignedPosition', -1)
                    if pos_val is None: pos_val = -1
                    
                    if status == 1 and pos_val >= 0:
                        pos_str = f"{pos_val:02d}"
                    else:
                        pos_str = "--"
                    
                    login = p.get('LoginCode', '????')
                    name = p.get('ParticipantName', 'Unknown')
                    
                    # Enum translations
                    status_text = PARTICIPATION_STATUS_MAP.get(status, '-')
                    
                    req_pos = p.get('RequestedPosition')
                    req_pos_text = PRESENTATION_POSITION_MAP.get(req_pos, '-') if req_pos is not None else '-'
                    
                    p_type = p.get('PresentationType')
                    type_text = PRESENTATION_TYPE_MAP.get(p_type, '-') if p_type is not None else '-'
                    
                    title = p.get('PresentationTitle') or '-'
                    orig_file = p.get('OriginalFileName') or '-'
                    url = p.get('ProjectUrl') or '-'
                    
                    # Format: 01 LoginCode Name: Status, Requested pos: ..., Type: ..., Title: ..., FileName: ..., Url: ...
                    line = f"{pos_str} {login} {name}: {status_text}, Requested pos: {req_pos_text}, Type: {type_text}, Title: {title}, FileName: {orig_file}, Url: {url}\n"
                    f.write(line)
        except IOError as e:
            print(f"Failed to write participants.txt: {e}")

    def update_participants_ui(self, participants):
        lines = []
        
        # Sort logic (same as write_participants_file)
        def sort_key(p):
            status = p.get('ParticipationStatus', 0)
            pos = p.get('AssignedPosition', -1)
            if pos is None: pos = -1
            login = p.get('LoginCode', '') or ''
            if status == 1 and pos >= 0:
                return (0, pos, login)
            return (1, 0, login)

        sorted_participants = sorted(participants, key=sort_key)

        for p in sorted_participants:
            status = p.get('ParticipationStatus', 0)
            # Only show participating users
            if status != 1:
                continue

            pos = p.get('AssignedPosition', -1)
            pos_str = f"{pos}." if pos is not None and pos >= 0 else "-."
            
            name = p.get('ParticipantName', 'Unknown')
            
            p_type = p.get('PresentationType')
            type_text = PRESENTATION_TYPE_MAP.get(p_type, 'Unknown')
            
            # Project details: Website or Filename
            detail = p.get('ProjectUrl')
            if not detail:
                detail = p.get('DownloadFileName')
            if not detail:
                detail = ""
                
            line = f"{pos_str} {name}: {type_text} ({detail})"
            lines.append(line)
            
        full_text = "\n".join(lines)
        self.root.after(0, lambda: self._set_text_content(full_text))

    def _set_text_content(self, text):
        self.participants_text.config(state=tk.NORMAL)
        self.participants_text.delete(1.0, tk.END)
        self.participants_text.insert(tk.END, text)
        self.participants_text.config(state=tk.DISABLED)

    def sync_presentation_files(self, status_result):
        folder = self.folder_var.get()
        download_path = status_result.get("DownloadPath", "")
        participants = status_result.get("Participants", [])
        
        history_path = os.path.join(folder, 'history')
        if not os.path.exists(history_path):
            os.makedirs(history_path)

        # 1. Calculate active expected files
        active_files = {} # filename -> p_data
        
        for p in participants:
            if p.get('ParticipationStatus') == 1:
                filename = self.build_filename(p)
                if filename:
                    active_files[filename] = p

        # 2. Manage files on disk
        try:
            files_on_disk = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
        except OSError as e:
            print(f"Cannot read sync folder: {e}")
            return

        # Archive obsolete files (files on disk not in active_files list)
        for f in files_on_disk:
            if f == 'participants.txt': continue
            if f not in active_files:
                print(f"[ARCHIVE] {f} -> history/")
                try:
                    shutil.move(os.path.join(folder, f), os.path.join(history_path, f))
                except Exception as e:
                    print(f"Error moving {f}: {e}")

        # Download or create missing active files
        for filename, p in active_files.items():
            filepath = os.path.join(folder, filename)
            if not os.path.exists(filepath):
                self.download_or_create_file(filepath, p, download_path)

    def sanitize_filename(self, name):
        return re.sub(r'[\\/*?:"<>|]', "", name).strip()

    def build_filename(self, p):
        try:
            # User request: Priority 1 - DownloadFileName from result
            server_file = p.get('DownloadFileName')
            if server_file:
                return server_file

            # User request: Priority 2 - Fallback
            # Format: GameKey+yyMMddHHmm+(space)+ParticipantName.txt
            
            game_key = p.get('GameKey', 'X')
            
            last_commit = p.get('LastCommit', 0)
            if last_commit:
                dt = datetime.fromtimestamp(last_commit / 1000.0)
                # yyMMddHHmm (assuming hh meant minutes as per standard usage, HH is hours)
                time_str = dt.strftime('%y%m%d%H%M')
            else:
                time_str = "0000000000"

            name = self.sanitize_filename(p.get('ParticipantName', 'NoName'))
            
            return f"{game_key}{time_str} {name}.txt"
        except Exception as e:
            print(f"Error building filename: {e}")
            return None

    def download_or_create_file(self, filepath, p, download_path):
        try:
            p_type = p.get('PresentationType')
            server_file = p.get('DownloadFileName')

            # File download (Scratch, AI, etc) if server has file
            if server_file and download_path:
                base_url = self.url_var.get().strip()
                if not base_url.endswith('/'): base_url += '/'
                
                # Handle relative download path from API
                # Typically API returns "downloads/" or similar relative to base
                relative_path = (download_path + server_file).strip('/')
                download_link = urljoin(base_url, relative_path)
                
                print(f"[DOWNLOAD] {download_link} -> {filepath}")
                res = self.session.get(download_link, stream=True, timeout=30)
                res.raise_for_status()
                with open(filepath, 'wb') as f:
                    for chunk in res.iter_content(chunk_size=8192):
                        f.write(chunk)
                return

            # Fallback text file (Microbit, or others without file/url)
            print(f"[CREATE] Text file: {filepath}")
            content = f"Presentation Type: {PRESENTATION_TYPE_MAP.get(p_type, 'Unknown')}\nName: {p.get('ParticipantName')}\n"
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)

        except Exception as e:
            print(f"[ERROR] Failed to create {filepath}: {e}")

    def reset_ui_to_stopped(self):
        if not self.is_syncing:
            self.update_status("Status: Stopped")
            self.sync_button.config(text="Start Sync", state=tk.NORMAL)
            self.browse_button.config(state=tk.NORMAL)

    def on_closing(self):
        if self.is_syncing:
            if messagebox.askokcancel("Quit", "Sync is running. Quit?"):
                self.is_syncing = False
                self.root.destroy()
        else:
            self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = SyncApp(root)
    root.mainloop()
