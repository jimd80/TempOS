#!/usr/bin/python3
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import os
import getpass
import http.server
import socketserver
import threading
import glob
from datetime import datetime
import shutil
import webbrowser
import urllib.parse
import argparse
import zipfile # For ZIP extraction
import json    # For API
import random  # For API
import string  # For API
import base64  # For API file data
import sys             # For script path and argv
import configparser    # For .ini settings files
import time            # For Ping delay

# --- Configuration ---
DEFAULT_PORT_ROOT = 80
DEFAULT_PORT_USER = 8080
DEFAULT_FILENAME = "index.html"

class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    app_instance = None

    def end_headers(self):
        """Inject CORS headers into every response."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        """Handle CORS preflight requests allowing any path."""
        self.send_response(200, "OK")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # --- Start of API Implementation ---

    def _send_json_response(self, data, status_code=200):
        """Helper to send a JSON response."""
        response_bytes = json.dumps(data).encode('utf-8')
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def _get_api_root(self):
        """Gets the validated API root path from the app instance."""
        if not self.app_instance:
            return None
        api_root = self.app_instance.api_fs_root.get().strip()
        if api_root and os.path.isdir(api_root):
            return api_root
        return None

    def _resolve_api_path(self, user_path):
        """
        Safely resolves a user-provided path against the API root.
        Returns the absolute real path if safe, otherwise None.
        """
        api_root = self._get_api_root()
        if not api_root:
            return None, "API filesystem is not configured or enabled on the server."

        # Normalize user_path: remove leading slashes and decode
        norm_user_path = urllib.parse.unquote(user_path).lstrip('/')

        # Join with the API root
        abs_api_root = os.path.abspath(api_root)
        unsafe_path = os.path.join(abs_api_root, norm_user_path)

        # The crucial security check: ensure the resolved path is within the API root
        real_path = os.path.abspath(unsafe_path)
        if os.path.commonpath([abs_api_root, real_path]) != abs_api_root:
            self.log_error("API path traversal attempt blocked: %s", user_path)
            return None, "Access denied: Path is outside the API root"

        return real_path, None

    def do_POST(self):
        """Handles all incoming POST requests, routing between Raw Write and API."""
        if not self.app_instance:
            self._send_json_response({"ErrorCode": 500, "Error": "Server internal error"}, 500)
            return

        # --- Raw Write Interception ---
        # Check if "Write Access" is enabled and if the path starts with the mapped web prefix
        map_prefix = self.app_instance.map_web_path_prefix.get().strip()
        if self.app_instance.write_access_enabled.get() and map_prefix and self.path.startswith(map_prefix):
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 0:
                    # Resolve where this file should actually go on disk
                    target_fs_path = self.translate_path(self.path)
                    
                    # Security check: prevent writing to directories or escaping mapped folder
                    if os.path.isdir(target_fs_path):
                        self.send_error(http.server.HTTPStatus.FORBIDDEN, "Cannot write to a directory")
                        return
                    
                    # Ensure parent directory exists (in case of new file in subfolder)
                    parent_dir = os.path.dirname(target_fs_path)
                    if not os.path.isdir(parent_dir):
                        os.makedirs(parent_dir, exist_ok=True)

                    post_data = self.rfile.read(content_length)
                    with open(target_fs_path, 'wb') as f:
                        f.write(post_data)
                    
                    self.app_instance.add_log_message(f"RAW POST: Wrote {content_length} bytes to {target_fs_path}")
                    self.send_response(http.server.HTTPStatus.OK)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            except Exception as e:
                self.send_error(http.server.HTTPStatus.INTERNAL_SERVER_ERROR, f"Write error: {e}")
                return

        # --- Standard JSON API Implementation ---
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            request_body = json.loads(post_data.decode('utf-8'))
        except (TypeError, ValueError, json.JSONDecodeError):
            self._send_json_response({"ErrorCode": 400, "Error": "Invalid JSON request body."}, 400)
            return

        method = request_body.get("Method")
        self.app_instance.add_log_message(f"API: Executing '{method}' (URL path '{self.path}' is ignored)")

        handler = getattr(self, f"_api_handle_{method}", self._api_handle_unknown)
        handler(request_body)

    def _api_handle_unknown(self, body):
        self._send_json_response({
            "ErrorCode": 400,
            "Error": f"Unknown method: {body.get('Method')}"
        }, 400)

    def _generate_random_alnum(self, length=20):
        return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

    def _api_handle_Ping(self, body):
        delay = body.get("Delay")
        if delay is not None:
            try:
                delay_sec = float(delay)
                if delay_sec > 0:
                    time.sleep(delay_sec)
            except (ValueError, TypeError):
                pass # Ignore invalid delay values

        js_timestamp = int(datetime.now().timestamp() * 1000)
        self._send_json_response({
            "ErrorCode": 0,
            "ServerTime": js_timestamp
        })

    def _api_handle_StartSession(self, body):
        js_timestamp = int(datetime.now().timestamp() * 1000)
        response = {
            "ErrorCode": 0,
            "ServerTime": js_timestamp,
            "SessionID": random.randint(100000, 999999),
            "SessionDuid": self._generate_random_alnum(),
            "ClientDuid": self._generate_random_alnum() # Per spec, return random
        }
        self._send_json_response(response)

    def _api_handle_ListFiles(self, body):
        user_path = body.get("Path")
        if user_path is None:
            self._send_json_response({"ErrorCode": 1, "Error": "Missing 'Path' parameter."}, 400)
            return

        real_path, err = self._resolve_api_path(user_path)
        if err:
            self._send_json_response({"ErrorCode": 2, "Error": err}, 403)
            return

        if not os.path.isdir(real_path):
            self._send_json_response({"ErrorCode": 3, "Error": "Path is not a directory or does not exist."}, 404)
            return

        try:
            files_list = []
            for name in os.listdir(real_path):
                entry_path = os.path.join(real_path, name)
                try:
                    stats = os.stat(entry_path)
                    is_folder = os.path.isdir(entry_path)
                    files_list.append({
                        "Name": name,
                        "Size": stats.st_size,
                        "Modified": int(stats.st_mtime * 1000),
                        "IsFolder": is_folder,
                        "IsReadOnly": not os.access(entry_path, os.W_OK)
                    })
                except FileNotFoundError:
                    continue # File might be deleted during scan
            self._send_json_response({"ErrorCode": 0, "Path": user_path, "Files": files_list})
        except OSError as e:
            self._send_json_response({"ErrorCode": 4, "Error": f"Cannot list directory: {e}"}, 500)

    def _api_handle_ReadFile(self, body):
        user_path = body.get("Path")
        start_pos = body.get("StartPos", 0)
        length = body.get("Length") # Can be null/None

        if user_path is None:
            return self._send_json_response({"ErrorCode": 1, "Error": "Missing 'Path' parameter."}, 400)

        real_path, err = self._resolve_api_path(user_path)
        if err:
            return self._send_json_response({"ErrorCode": 2, "Error": err}, 403)

        if not os.path.isfile(real_path):
            return self._send_json_response({"ErrorCode": 3, "Error": "Path is not a file or does not exist."}, 404)

        try:
            with open(real_path, 'rb') as f:
                f.seek(start_pos)
                data_bytes = f.read(length) if length is not None else f.read()

            # Base64 encode for safe JSON transport of binary data
            encoded_data = base64.b64encode(data_bytes).decode('ascii')

            response = {
                "ErrorCode": 0,
                "Path": user_path,
                "Data": encoded_data,
            }
            self._send_json_response(response)
        except OSError as e:
            self._send_json_response({"ErrorCode": 4, "Error": f"Cannot read file: {e}"}, 500)

    def _api_handle_WriteFile(self, body):
        user_path = body.get("Path")
        data_b64 = body.get("Data")
        append = body.get("Append", False)
        overwrite = body.get("Overwrite", True)

        if user_path is None or data_b64 is None:
            return self._send_json_response({"ErrorCode": 1, "Error": "Missing 'Path' or 'Data' parameter."}, 400)

        real_path, err = self._resolve_api_path(user_path)
        if err:
            return self._send_json_response({"ErrorCode": 2, "Error": err}, 403)

        if os.path.isdir(real_path):
            return self._send_json_response({"ErrorCode": 5, "Error": "Cannot write to a directory."}, 400)

        parent_dir = os.path.dirname(real_path)
        if not os.path.isdir(parent_dir):
            return self._send_json_response({"ErrorCode": 6, "Error": "Parent directory does not exist."}, 404)

        if os.path.exists(real_path) and not overwrite and not append:
            return self._send_json_response({"ErrorCode": 7, "Error": "File exists and Overwrite is false."}, 409) # 409 Conflict

        try:
            # Decode the Base64 data back to binary
            file_data = base64.b64decode(data_b64)
        except (ValueError, TypeError):
            return self._send_json_response({"ErrorCode": 8, "Error": "Invalid Base64 data."}, 400)

        mode = 'ab' if append else 'wb'
        try:
            with open(real_path, mode) as f:
                f.write(file_data)

            new_mtime = int(os.path.getmtime(real_path) * 1000)
            response = {
                "ErrorCode": 0,
                "Path": user_path,
                "NewModifiedTime": new_mtime
            }
            self._send_json_response(response)
        except OSError as e:
            self._send_json_response({"ErrorCode": 4, "Error": f"Cannot write file: {e}"}, 500)

    # --- End of API Implementation ---

    def __init__(self, *args, **kwargs):
        if CustomHTTPRequestHandler.app_instance:
            CustomHTTPRequestHandler.app_instance.increment_active_connections()
            self.directory = CustomHTTPRequestHandler.app_instance.current_serve_path.get()
        else:
            self.directory = os.getcwd()
            print("Warning: CustomHTTPRequestHandler.app_instance not set. Serving from CWD.")
        self.original_request_path = None
        super().__init__(*args, directory=self.directory, **kwargs)
    def translate_path(self, path):
        path = path.split('?',1)[0]
        path = path.split('#',1)[0]
        trailing_slash = path.endswith('/')
        path = urllib.parse.unquote(path)
        parts = [part for part in path.split('/') if part]
        if self.app_instance:
            map_web_prefix_str = self.app_instance.map_web_path_prefix.get().strip()
            map_local_folder_str = self.app_instance.map_local_folder.get().strip()
            if map_web_prefix_str and map_local_folder_str and os.path.isdir(map_local_folder_str):
                normalized_web_prefix_parts = [part for part in map_web_prefix_str.strip('/').split('/') if part]
                if len(parts) >= len(normalized_web_prefix_parts) and \
                   parts[:len(normalized_web_prefix_parts)] == normalized_web_prefix_parts:
                    remaining_path_parts = parts[len(normalized_web_prefix_parts):]
                    local_path_segment = os.path.join(*remaining_path_parts) if remaining_path_parts else ""
                    translated = os.path.join(map_local_folder_str, local_path_segment)
                    if os.path.commonpath([map_local_folder_str, os.path.abspath(translated)]) != os.path.normpath(map_local_folder_str):
                        self.log_error("Attempt to escape mapped directory: %s", path)
                    else:
                        if trailing_slash and os.path.isdir(translated) and not translated.endswith(os.sep):
                             translated += os.sep
                        return translated
        return super().translate_path(path)
    def send_head(self):
        path = self.translate_path(self.path)
        f = None
        if os.path.isdir(path):
            parts = urllib.parse.urlsplit(self.path)
            if not parts.path.endswith('/'):
                self.send_response(http.server.HTTPStatus.MOVED_PERMANENTLY)
                new_parts = (parts.scheme, parts.netloc, parts.path + '/', parts.query, parts.fragment)
                new_url = urllib.parse.urlunsplit(new_parts)
                self.send_header("Location", new_url); self.send_header("Content-Length", "0"); self.end_headers()
                return None
        ctype = self.guess_type(path)
        try: f = open(path, 'rb')
        except FileNotFoundError:
            self.send_error(http.server.HTTPStatus.NOT_FOUND, "File not found")
            if self.app_instance: self.app_instance.last_attempted_file_for_request[self.client_address] = os.path.basename(path)
            return None
        except IsADirectoryError:
            self.send_error(http.server.HTTPStatus.NOT_FOUND, "Directory listing not supported or no default file found")
            if self.app_instance: self.app_instance.last_attempted_file_for_request[self.client_address] = "(directory)"
            return None
        except OSError:
            self.send_error(http.server.HTTPStatus.INTERNAL_SERVER_ERROR, "Error accessing file")
            if self.app_instance: self.app_instance.last_attempted_file_for_request[self.client_address] = os.path.basename(path)
            return None
        try:
            self.send_response(http.server.HTTPStatus.OK)
            self.send_header("Content-type", ctype)
            fs = os.fstat(f.fileno())
            self.send_header("Content-Length", str(fs[6]))
            self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            chosen_file_for_log = os.path.basename(self.path.strip('/'))
            if not chosen_file_for_log and self.path.strip('/') == "":
                if os.path.isfile(path): chosen_file_for_log = os.path.basename(path)
            if self.app_instance: self.app_instance.last_attempted_file_for_request[self.client_address] = chosen_file_for_log or "(unknown file)"
            return f
        except: f.close(); raise
    def finish(self, *args, **kwargs):
        try: super().finish(*args, **kwargs)
        finally:
            if CustomHTTPRequestHandler.app_instance:
                CustomHTTPRequestHandler.app_instance.decrement_active_connections()
                CustomHTTPRequestHandler.app_instance.last_attempted_file_for_request.pop(self.client_address, None)
                
    def _serve_flat_directory_list(self, dir_path):
        """Generates and serves a flat text list of a directory's contents."""
        try:
            entries = os.listdir(dir_path)
        except OSError:
            if self.app_instance:
                self.app_instance.last_attempted_file_for_request[self.client_address] = "(dir read error)"
            self.send_error(http.server.HTTPStatus.NOT_FOUND, "Cannot list directory")
            return

        entries.sort()
        lines = []
        for entry in entries:
            full_path = os.path.join(dir_path, entry)
            if os.path.isdir(full_path):
                lines.append(entry + "/")
            else:
                lines.append(entry)
                
        text_content = "\n".join(lines) + ("\n" if lines else "")
        encoded = text_content.encode('utf-8')
        
        # Set this before send_response so logging captures it
        if self.app_instance:
            self.app_instance.last_attempted_file_for_request[self.client_address] = "(flat directory list)"
            
        self.send_response(http.server.HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.original_request_path is None: self.original_request_path = self.path
        if self.app_instance and self.app_instance.is_scan_active():
            copied_result = self.app_instance._check_and_process_newer_file_from_scan_folder()
            if copied_result:
                was_processed, processed_filename, operation = copied_result
                if was_processed:
                    timestamp = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
                    status_msg = f"{operation.capitalize()} '{processed_filename}' at {timestamp}"
                    self.app_instance.update_status_message(status_msg)

        current_request_path_for_logic = self.path
        path_parts_cleaned = current_request_path_for_logic.split('?',1)[0].split('#',1)[0]
        fs_path_to_target = self.translate_path(path_parts_cleaned)

        # --- Map Web Path Directory Interception ---
        is_mapped_dir = False
        if self.app_instance:
            map_web_prefix_str = self.app_instance.map_web_path_prefix.get().strip()
            map_local_folder_str = self.app_instance.map_local_folder.get().strip()
            if map_web_prefix_str and map_local_folder_str and os.path.isdir(map_local_folder_str):
                # Check if the URL requested starts with the mapped web prefix
                req_path = urllib.parse.urlsplit(self.path).path
                normalized_req = [p for p in req_path.split('/') if p]
                normalized_prefix = [p for p in map_web_prefix_str.strip('/').split('/') if p]
                
                if len(normalized_req) >= len(normalized_prefix) and \
                   normalized_req[:len(normalized_prefix)] == normalized_prefix:
                    # It falls under the mapped space. Is it a directory?
                    if os.path.isdir(fs_path_to_target):
                        is_mapped_dir = True

        if is_mapped_dir:
            parts = urllib.parse.urlsplit(self.path)
            # Standard redirect if the user forgets the trailing slash on a folder
            if not parts.path.endswith('/'):
                if self.app_instance:
                    self.app_instance.last_attempted_file_for_request[self.client_address] = "(redirect to slash)"
                self.send_response(http.server.HTTPStatus.MOVED_PERMANENTLY)
                new_parts = (parts.scheme, parts.netloc, parts.path + '/', parts.query, parts.fragment)
                self.send_header("Location", urllib.parse.urlunsplit(new_parts))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            # Serve the flat file/folder plain text list
            self._serve_flat_directory_list(fs_path_to_target)
            return
        # --- End Interception ---

        basename_cleaned = os.path.basename(path_parts_cleaned)
        is_dir_request = ('.' not in basename_cleaned and basename_cleaned != "") or path_parts_cleaned.endswith('/')
        
        if self.app_instance :
             self.app_instance.last_attempted_file_for_request[self.client_address] = os.path.basename(self.path.strip('/')) \
                                                                                    if not is_dir_request else None
        
        if is_dir_request:
            if os.path.isdir(fs_path_to_target):
                target_file_name = None
                if self.app_instance.current_use_latest_html.get():
                    latest_file_path, _ = self.app_instance._get_latest_file_in_folder(fs_path_to_target, "html")
                    if latest_file_path:
                        target_file_name = os.path.basename(latest_file_path)
                else:
                    target_file_name = self.app_instance.current_default_file_real.get()
                if target_file_name:
                    if self.app_instance: self.app_instance.last_attempted_file_for_request[self.client_address] = target_file_name
                    if os.path.exists(os.path.join(fs_path_to_target, target_file_name)):
                        if path_parts_cleaned.endswith('/'): self.path = path_parts_cleaned + target_file_name
                        else: self.path = path_parts_cleaned + '/' + target_file_name
                        self.path = os.path.normpath(self.path)
                        if not self.path.startswith('/') and target_file_name == self.path: self.path = '/' + target_file_name
                elif self.app_instance:
                    self.app_instance.last_attempted_file_for_request[self.client_address] = "(no candidate file)"
        
        if self.app_instance and self.app_instance.last_attempted_file_for_request.get(self.client_address) is None:
            self.app_instance.last_attempted_file_for_request[self.client_address] = os.path.basename(self.path.strip('/')) \
                                                                                         if self.path.strip('/') else '(root request)'
        super().do_GET()
        
    def log_request(self, code='-', size='-'):
        if isinstance(code, http.server.HTTPStatus): code = code.value
        # For API requests handled via do_POST, this log isn't as useful,
        # so we let our custom API logger handle it. OPTIONS is for CORS.
        if self.command in ('POST', 'OPTIONS'):
            return
        displayed_request_path = self.original_request_path if self.original_request_path else self.path
        log_msg = f"{code} {self.command} {displayed_request_path}"
        attempted_file = None
        if self.app_instance:
            attempted_file = self.app_instance.last_attempted_file_for_request.get(self.client_address)
        if attempted_file:
            display_attempted_file = attempted_file if attempted_file != "" else (self.app_instance.current_default_file_real.get() if self.app_instance else DEFAULT_FILENAME)
            if isinstance(code, int) and code < 400 : log_msg += f" (fetched: {display_attempted_file})"
            elif isinstance(code, int) and code >=400: log_msg += f" (not found: {display_attempted_file})"
        if isinstance(code, int) and code >= 400 and self.responses:
            error_explanation = self.responses.get(code, (None, ""))[1]
            if error_explanation and not attempted_file : log_msg += f" - Error: {error_explanation}"
        if self.app_instance and self.app_instance.master:
            try: self.app_instance.master.after(0, self.app_instance.add_log_message, log_msg)
            except tk.TclError: pass
    def log_error(self, format_str, *args): pass

class WebServerApp:
    def __init__(self, master, cli_args=None):
        self.master = master
        master.title("Dev Web Server")

        self.httpd = None
        self.server_thread = None
        self._active_connections = 0
        self.active_connections_var = tk.IntVar(value=0)

        # --- Load Configuration from .ini or CLI arguments ---
        config = self._load_configuration(cli_args)

        # --- Set Initial Values from Configuration ---
        initial_serve_path = config['servefolder']
        if not os.path.isdir(initial_serve_path):
            print(f"Warning: Configured servefolder '{initial_serve_path}' not found. Using CWD.")
            initial_serve_path = os.getcwd()

        initial_default_file = config['defaultfile']
        initial_port_str = config['port']
        initial_update_path_display = config['updatepath']
        u_path = initial_update_path_display.strip('/')
        initial_update_path_internal = u_path.strip(os.sep)

        initial_update_source = config['updatesource']
        if initial_update_source and not os.path.isdir(initial_update_source):
            print(f"Warning: Configured updatesource '{initial_update_source}' not found. Scan inactive.")
            initial_update_source = ""

        initial_use_latest = config['uselatest']
        initial_map_web_path = config['mapwebpath']
        initial_map_local_folder = config['maplocalfolder']

        if initial_map_web_path and not initial_map_web_path.startswith("/"):
            print(f"Warning: mapwebpath '{initial_map_web_path}' must start with '/'. Ignored.")
            initial_map_web_path = ""; initial_map_local_folder = ""
        if initial_map_web_path and initial_map_local_folder and not os.path.isdir(initial_map_local_folder):
            print(f"Warning: maplocalfolder '{initial_map_local_folder}' not found. Ignored.")
            initial_map_web_path = ""; initial_map_local_folder = ""

        initial_scan_file_type = config['scanfiletype']
        initial_api_fs_root = config['fsroot']
        if initial_api_fs_root and not os.path.isdir(initial_api_fs_root):
            print(f"Warning: Configured fsroot '{initial_api_fs_root}' not found. Filesystem API will be disabled.")
            initial_api_fs_root = ""

        # --- Initialize Tkinter Variables ---
        self.current_serve_path = tk.StringVar(value=initial_serve_path)
        self.current_port = tk.StringVar(value=initial_port_str)
        self.current_default_file_display = tk.StringVar(value=initial_default_file)
        self.current_default_file_real = tk.StringVar(value=initial_default_file)
        self.current_use_latest_html = tk.BooleanVar(value=initial_use_latest)
        self.update_web_subpath_display = tk.StringVar(value=initial_update_path_display)
        self.update_web_subpath_internal = initial_update_path_internal
        self.scan_source_path = tk.StringVar(value=initial_update_source)
        self.scan_file_type = tk.StringVar(value=initial_scan_file_type)
        self.map_web_path_prefix = tk.StringVar(value=initial_map_web_path)
        self.map_local_folder = tk.StringVar(value=initial_map_local_folder)
        self.write_access_enabled = tk.BooleanVar(value=config['writeaccess'])
        self.api_fs_root = tk.StringVar(value=initial_api_fs_root)
        self.last_scanned_source_file_path = None
        self.last_scanned_source_file_mtime = 0.0
        self.status_message_var = tk.StringVar(value="Web server stopped")
        self.server_url_var = tk.StringVar(value="")
        self.last_attempted_file_for_request = {}
        CustomHTTPRequestHandler.app_instance = self

        # --- GUI Elements ---
        row1_frame = tk.Frame(master); row1_frame.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(row1_frame, text="Serve from Path:").pack(side=tk.LEFT)
        self.path_entry = tk.Entry(row1_frame, textvariable=self.current_serve_path, width=50); self.path_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.select_path_button = tk.Button(row1_frame, text="Select...", command=self.select_serve_path); self.select_path_button.pack(side=tk.LEFT, padx=(5,0))

        row2_frame = tk.Frame(master); row2_frame.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(row2_frame, text="Default File:").pack(side=tk.LEFT, padx=(0,2))
        self.default_file_entry = tk.Entry(row2_frame, textvariable=self.current_default_file_display, width=30); self.default_file_entry.pack(side=tk.LEFT, padx=(0,10))
        self.use_latest_checkbox = tk.Checkbutton(row2_frame, text="Use latest HTML in folder", variable=self.current_use_latest_html, command=self.on_use_latest_toggled); self.use_latest_checkbox.pack(side=tk.LEFT)

        row3_frame = tk.Frame(master); row3_frame.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(row3_frame, text="Update Web Path:").pack(side=tk.LEFT, padx=(0,2))
        self.update_subpath_entry = tk.Entry(row3_frame, textvariable=self.update_web_subpath_display, width=35); self.update_subpath_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.select_update_subpath_button = tk.Button(row3_frame, text="Select Subpath...", command=self.select_update_subpath); self.select_update_subpath_button.pack(side=tk.LEFT, padx=(5,0))

        row4_frame = tk.Frame(master); row4_frame.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(row4_frame, text="With new file from:").pack(side=tk.LEFT, padx=(0,2))
        self.scan_source_entry = tk.Entry(row4_frame, textvariable=self.scan_source_path, width=30); self.scan_source_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)

        self.scan_type_frame = tk.Frame(row4_frame)
        self.scan_type_frame.pack(side=tk.LEFT, padx=(5,0))
        tk.Radiobutton(self.scan_type_frame, text="HTML", variable=self.scan_file_type, value="html", command=self.on_scan_type_changed).pack(side=tk.LEFT)
        tk.Radiobutton(self.scan_type_frame, text="ZIP", variable=self.scan_file_type, value="zip", command=self.on_scan_type_changed).pack(side=tk.LEFT)

        self.select_scan_source_button = tk.Button(row4_frame, text="Select Source...", command=self.select_scan_source_path); self.select_scan_source_button.pack(side=tk.LEFT, padx=(5,0))

        map_frame = tk.Frame(master); map_frame.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(map_frame, text="Map Web Path:").pack(side=tk.LEFT, padx=(0,2))
        self.map_web_path_entry = tk.Entry(map_frame, textvariable=self.map_web_path_prefix, width=15); self.map_web_path_entry.pack(side=tk.LEFT, padx=(0,5))
        tk.Label(map_frame, text="To Local Folder:").pack(side=tk.LEFT, padx=(0,2))
        self.map_local_folder_entry = tk.Entry(map_frame, textvariable=self.map_local_folder, width=25); self.map_local_folder_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.select_map_local_button = tk.Button(map_frame, text="Select...", command=self.select_map_local_folder); self.select_map_local_button.pack(side=tk.LEFT, padx=(5,0))
        self.write_access_checkbox = tk.Checkbutton(map_frame, text="Write access", variable=self.write_access_enabled); self.write_access_checkbox.pack(side=tk.LEFT, padx=(10,0))

        api_frame = tk.Frame(master); api_frame.pack(fill=tk.X, padx=5, pady=2)
        tk.Label(api_frame, text="API Filesystem Root:").pack(side=tk.LEFT)
        self.api_fs_root_entry = tk.Entry(api_frame, textvariable=self.api_fs_root, width=50); self.api_fs_root_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.select_api_fs_root_button = tk.Button(api_frame, text="Select...", command=self.select_api_fs_root); self.select_api_fs_root_button.pack(side=tk.LEFT, padx=(5,0))

        row6_frame = tk.Frame(master); row6_frame.pack(fill=tk.X, padx=5, pady=(5,2))
        # Left-aligned items
        tk.Label(row6_frame, text="Port:").pack(side=tk.LEFT, padx=(0,2))
        self.port_entry = tk.Entry(row6_frame, textvariable=self.current_port, width=6); self.port_entry.pack(side=tk.LEFT, padx=(0,10))
        self.server_button = tk.Button(row6_frame, text="Start Server", command=self.toggle_server, bg="lightgreen", width=12); self.server_button.pack(side=tk.LEFT, padx=(0,5))
        self.url_label = tk.Label(row6_frame, textvariable=self.server_url_var, fg="blue", cursor="hand2"); self.url_label.pack(side=tk.LEFT, padx=(0,10))
        self.url_label.bind("<Button-1>", self.open_server_url)
        # Right-aligned item
        self.save_button = tk.Button(row6_frame, text="Save Settings", command=self.save_settings); self.save_button.pack(side=tk.RIGHT, padx=(5,0))

        # --- New Status Bar with Connections ---
        status_frame = tk.Frame(master, relief=tk.SUNKEN, bd=1)
        status_frame.pack(fill=tk.X, padx=5, pady=(2,2))
        self.status_label = tk.Label(status_frame, textvariable=self.status_message_var, anchor=tk.W, padx=2)
        self.status_label.pack(side=tk.LEFT, expand=True, fill=tk.X)
        separator = tk.Frame(status_frame, width=2, bd=1, relief=tk.SUNKEN); separator.pack(side=tk.RIGHT, fill=tk.Y, padx=(2,5), pady=2)
        self.connections_value_label = tk.Label(status_frame, textvariable=self.active_connections_var); self.connections_value_label.pack(side=tk.RIGHT, padx=(0, 5))
        tk.Label(status_frame, text="Connections:").pack(side=tk.RIGHT)

        tk.Label(master, text="Log:").pack(anchor=tk.W, padx=5, pady=(0,2))
        self.log_text = scrolledtext.ScrolledText(master, height=7, width=80, state=tk.DISABLED); self.log_text.pack(padx=5, pady=(0,5), fill=tk.BOTH, expand=True)

        master.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.scan_source_path.trace_add("write", self.on_scan_source_path_changed)
        self.update_web_subpath_display.trace_add("write", self.on_update_subpath_display_changed)
        self.current_serve_path.trace_add("write", self.on_serve_path_changed)
        self.current_default_file_display.trace_add("write", self.on_default_file_display_changed)
        self.map_web_path_prefix.trace_add("write", self.on_map_path_changed)
        self.map_local_folder.trace_add("write", self.on_map_path_changed)
        self.on_use_latest_toggled()
        if cli_args and cli_args.start: self.master.after(100, self.start_server)

    def _load_configuration(self, cli_args):
        # --- Define defaults first ---
        defaults = {
            'servefolder': os.getcwd(),
            'defaultfile': DEFAULT_FILENAME,
            'updatepath': '/',
            'updatesource': '',
            'scanfiletype': 'html',
            'port': str(DEFAULT_PORT_USER),
            'uselatest': False,
            'mapwebpath': '',
            'maplocalfolder': '',
            'fsroot': '',
            'writeaccess': False
        }
        if os.name != 'nt' and getpass.getuser() == 'root': defaults['port'] = str(DEFAULT_PORT_ROOT)
        elif os.name == 'nt' and os.environ.get("USERNAME", "").upper() == "SYSTEM": defaults['port'] = str(DEFAULT_PORT_ROOT)

        # --- If any command line parameter is used, skip .ini load ---
        if len(sys.argv) > 1:
            print("Command-line arguments detected, skipping .ini file.")
            update_path_cli = "/" + cli_args.updatepath.strip('/') if cli_args.updatepath else defaults['updatepath']
            return {
                'servefolder': cli_args.servefolder if cli_args.servefolder else defaults['servefolder'],
                'defaultfile': cli_args.defaultfile if cli_args.defaultfile else defaults['defaultfile'],
                'updatepath': update_path_cli,
                'updatesource': cli_args.updatesource if cli_args.updatesource else defaults['updatesource'],
                'scanfiletype': cli_args.scanfiletype if cli_args.scanfiletype else defaults['scanfiletype'],
                'port': str(cli_args.port) if cli_args.port is not None else defaults['port'],
                'uselatest': cli_args.uselatest,
                'mapwebpath': cli_args.mapwebpath if cli_args.mapwebpath else defaults['mapwebpath'],
                'maplocalfolder': cli_args.maplocalfolder if cli_args.maplocalfolder else defaults['maplocalfolder'],
                'fsroot': cli_args.fsroot if cli_args.fsroot else defaults['fsroot'],
                'writeaccess': defaults['writeaccess'] # CLI doesn't have writeaccess flag currently
            }

        # --- If no CLI, try to load from INI file ---
        config = configparser.ConfigParser()
        script_basename = os.path.basename(sys.argv[0])
        script_name_no_ext, _ = os.path.splitext(script_basename)
        ini_filename = f"{script_name_no_ext}.ini"
        
        cwd_ini_path = os.path.join(os.getcwd(), ini_filename)
        script_dir_ini_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), ini_filename)
        
        ini_path_to_load = None
        if os.path.exists(cwd_ini_path): ini_path_to_load = cwd_ini_path
        elif os.path.exists(script_dir_ini_path): ini_path_to_load = script_dir_ini_path
        
        if ini_path_to_load and config.read(ini_path_to_load):
            print(f"Loading settings from: {ini_path_to_load}")
            if 'Settings' in config:
                s = config['Settings']
                return {key: s.get(key, defaults[key]) for key in defaults if key not in ['uselatest', 'writeaccess']} | \
                       {'uselatest': s.getboolean('uselatest', defaults['uselatest']), 
                        'writeaccess': s.getboolean('writeaccess', defaults['writeaccess'])}

        # --- If no CLI and no INI, use defaults ---
        print("No command-line arguments or .ini file found, using defaults.")
        return defaults

    def save_settings(self):
        config = configparser.ConfigParser()
        config['Settings'] = {
            'servefolder': self.current_serve_path.get(),
            'defaultfile': self.current_default_file_display.get(),
            'uselatest': str(self.current_use_latest_html.get()),
            'updatepath': self.update_web_subpath_display.get(),
            'updatesource': self.scan_source_path.get(),
            'scanfiletype': self.scan_file_type.get(),
            'mapwebpath': self.map_web_path_prefix.get(),
            'maplocalfolder': self.map_local_folder.get(),
            'fsroot': self.api_fs_root.get(),
            'port': self.current_port.get(),
            'writeaccess': str(self.write_access_enabled.get())
        }

        script_basename = os.path.basename(sys.argv[0])
        script_name_no_ext, _ = os.path.splitext(script_basename)
        ini_filename = f"{script_name_no_ext}.ini"

        filepath = filedialog.asksaveasfilename(
            initialdir=os.getcwd(),
            initialfile=ini_filename,
            defaultextension=".ini",
            filetypes=[("INI files", "*.ini"), ("All files", "*.*")]
        )

        if filepath:
            try:
                with open(filepath, 'w') as configfile:
                    config.write(configfile)
                self.add_log_message(f"Settings saved to '{os.path.basename(filepath)}'")
            except Exception as e:
                messagebox.showerror("Save Error", f"Failed to save settings: {e}")

    def select_api_fs_root(self):
        """Opens a dialog to select the API filesystem root directory."""
        if self.httpd:
            messagebox.showwarning("Server Running", "Cannot change API root while server is running.")
            return
        dirname = filedialog.askdirectory(initialdir=self.api_fs_root.get() or os.path.expanduser("~"))
        if dirname:
            self.api_fs_root.set(dirname)

    def on_scan_type_changed(self, *args): # ... (no changes)
        self.last_scanned_source_file_path = None
        self.last_scanned_source_file_mtime = 0.0
        if self.scan_file_type.get() == "html": self.on_use_latest_toggled()
        elif self.current_use_latest_html.get(): self.current_default_file_display.set(" (ZIP mode)")
    def on_map_path_changed(self, *args): # ... (no changes)
        prefix = self.map_web_path_prefix.get()
        if prefix and not prefix.startswith("/"): self.map_web_path_prefix.set("/" + prefix)
    def select_map_local_folder(self): # ... (no changes)
        if self.httpd: messagebox.showwarning("Server Running", "Cannot change path mapping while server is running."); return
        dirname = filedialog.askdirectory(initialdir=self.map_local_folder.get() or os.path.expanduser("~"))
        if dirname: self.map_local_folder.set(dirname)
    def open_server_url(self, event=None): # ... (no changes)
        url = self.server_url_var.get(); webbrowser.open_new_tab(url) if url else None
    def on_default_file_display_changed(self, *args): # ... (no changes)
        if not self.current_use_latest_html.get(): self.current_default_file_real.set(self.current_default_file_display.get())
    def update_status_message(self, message): # ... (no changes)
        self.status_message_var.set(message)
    def on_serve_path_changed(self, *args): # ... (no changes)
        if self.update_web_subpath_display.get() == "/": self.update_web_subpath_internal = ""
        self.on_use_latest_toggled(); self.update_clickable_url()
    def on_update_subpath_display_changed(self, *args): # ... (no changes)
        display_path = self.update_web_subpath_display.get()
        if display_path.startswith("/"): self.update_web_subpath_internal = display_path[1:].strip(os.sep)
        else:
            self.update_web_subpath_internal = display_path.strip(os.sep)
            if self.update_web_subpath_internal: self.update_web_subpath_display.set("/" + self.update_web_subpath_internal)
            else: self.update_web_subpath_display.set("/")
        self.on_use_latest_toggled(); self.update_clickable_url()
    def on_scan_source_path_changed(self, *args): # ... (no changes)
        self.on_use_latest_toggled()
    def on_use_latest_toggled(self, *args): # ... (no changes)
        serve_path_str = self.current_serve_path.get()
        target_dir_for_latest_display = os.path.join(serve_path_str, self.update_web_subpath_internal)
        if self.current_use_latest_html.get():
            self.default_file_entry.config(state=tk.DISABLED)
            if self.scan_file_type.get() == "html":
                if os.path.isdir(target_dir_for_latest_display):
                    latest_file, _ = self._get_latest_file_in_folder(target_dir_for_latest_display, "html")
                    self.current_default_file_display.set(os.path.basename(latest_file) if latest_file else " (No HTML found)")
                else: self.current_default_file_display.set(" (Update path invalid)")
            else:
                self.current_default_file_display.set(" (ZIP mode: latest applies to content)")
        else:
            self.default_file_entry.config(state=tk.NORMAL)
            self.current_default_file_display.set(self.current_default_file_real.get())
        if self.httpd: self.use_latest_checkbox.config(state=tk.DISABLED)
        else: self.use_latest_checkbox.config(state=tk.NORMAL)
    def is_scan_active(self): # ... (no changes)
        return bool(self.scan_source_path.get() and os.path.isdir(self.scan_source_path.get()))
    def increment_active_connections(self): # ... (no changes)
        self._active_connections += 1
        if self.master.winfo_exists(): self.master.after(0, lambda: self.active_connections_var.set(self._active_connections))
    def decrement_active_connections(self): # ... (no changes)
        self._active_connections -= 1
        if self._active_connections < 0: self._active_connections = 0
        if self.master.winfo_exists(): self.master.after(0, lambda: self.active_connections_var.set(self._active_connections))
    def _get_latest_file_in_folder(self, folder_path, file_type="html"): # ... (no changes)
        if not os.path.isdir(folder_path): return None, 0.0
        patterns = ['*.html', '*.htm'] if file_type == "html" else ['*.zip'] if file_type == "zip" else []
        if not patterns: return None, 0.0
        found_files = []
        for pattern in patterns:
            try: found_files.extend(glob.glob(os.path.join(folder_path, pattern)))
            except Exception as e: self.add_log_message(f"Error accessing folder {folder_path} for {pattern}: {e}")
        if not found_files: return None, 0.0
        try: latest_file_path = max(found_files, key=os.path.getmtime); return latest_file_path, os.path.getmtime(latest_file_path)
        except (FileNotFoundError, ValueError): return None, 0.0
    def _perform_initial_scan(self): # ... (no changes)
        scan_dir = self.scan_source_path.get(); scan_type = self.scan_file_type.get()
        if not scan_dir or not os.path.isdir(scan_dir): self.add_log_message(f"SCAN: Source path not set or invalid. Auto-update disabled."); return False
        self.add_log_message(f"SCAN: Performing initial scan of '{scan_dir}' for *.{scan_type} files...")
        latest_path, self.last_scanned_source_file_mtime = self._get_latest_file_in_folder(scan_dir, scan_type)
        self.last_scanned_source_file_path = latest_path
        if latest_path: self.add_log_message(f"SCAN: Initial latest *.{scan_type}: '{os.path.basename(latest_path)}'")
        else: self.add_log_message(f"SCAN: No *.{scan_type} files found in '{scan_dir}'.")
        return True
    def _is_path_safe(self, target_dir, member_path): # ... (no changes)
        target_dir_abs = os.path.abspath(target_dir)
        prospective_path_abs = os.path.abspath(os.path.join(target_dir_abs, member_path))
        return os.path.commonpath([target_dir_abs, prospective_path_abs]) == target_dir_abs
    def _check_and_process_newer_file_from_scan_folder(self): # ... (no changes)
        if not self.is_scan_active(): return (False, None, None)
        scan_dir = self.scan_source_path.get(); scan_type = self.scan_file_type.get()
        current_latest_scan_path, current_latest_scan_mtime = self._get_latest_file_in_folder(scan_dir, scan_type)
        if not current_latest_scan_path: return (False, None, None)
        original_filename = os.path.basename(current_latest_scan_path)
        should_process = (current_latest_scan_mtime > self.last_scanned_source_file_mtime) or \
                         (current_latest_scan_path != self.last_scanned_source_file_path and current_latest_scan_mtime >= self.last_scanned_source_file_mtime)
        if should_process:
            base_serve_path = self.current_serve_path.get(); relative_update_path = self.update_web_subpath_internal
            full_dest_dir = os.path.join(base_serve_path, relative_update_path); os.makedirs(full_dest_dir, exist_ok=True)
            if scan_type == "html":
                dest_filename = original_filename if self.current_use_latest_html.get() else self.current_default_file_real.get()
                dest_file_path = os.path.join(full_dest_dir, dest_filename)
                try:
                    shutil.copy2(current_latest_scan_path, dest_file_path)
                    self.add_log_message(f"SCAN: Copied '{original_filename}' as '{dest_filename}' to '{self.update_web_subpath_display.get()}'")
                    self.last_scanned_source_file_path = current_latest_scan_path; self.last_scanned_source_file_mtime = current_latest_scan_mtime
                    if self.current_use_latest_html.get(): self.master.after(0, self.on_use_latest_toggled)
                    return (True, original_filename, "copied")
                except Exception as e: self.add_log_message(f"SCAN: Error copying '{original_filename}' to '{dest_file_path}': {e}")
            elif scan_type == "zip":
                try:
                    with zipfile.ZipFile(current_latest_scan_path, 'r') as zip_ref:
                        members_to_extract = [m for m in zip_ref.namelist() if self._is_path_safe(full_dest_dir, m)]
                        skipped_count = len(zip_ref.namelist()) - len(members_to_extract)
                        if skipped_count > 0: self.add_log_message(f"SCAN: Skipped {skipped_count} unsafe path(s) in '{original_filename}'")
                        if members_to_extract: zip_ref.extractall(full_dest_dir, members=members_to_extract)
                    self.add_log_message(f"SCAN: Extracted '{original_filename}' to '{self.update_web_subpath_display.get()}'")
                    self.last_scanned_source_file_path = current_latest_scan_path; self.last_scanned_source_file_mtime = current_latest_scan_mtime
                    if self.current_use_latest_html.get(): self.master.after(0, self.on_use_latest_toggled)
                    return (True, original_filename, "extracted")
                except zipfile.BadZipFile: self.add_log_message(f"SCAN: Error - '{original_filename}' is not a valid ZIP file.")
                except Exception as e: self.add_log_message(f"SCAN: Error extracting '{original_filename}': {e}")
        return (False, None, None) # No file processed
    def select_serve_path(self): # ... (no changes)
        if self.httpd: messagebox.showwarning("Server Running", "Cannot change serve path."); return
        dirname = filedialog.askdirectory(initialdir=self.current_serve_path.get())
        if dirname: self.current_serve_path.set(dirname)
    def select_update_subpath(self): # ... (no changes)
        if self.httpd: messagebox.showwarning("Server Running", "Cannot change update subpath."); return
        current_serve_dir = self.current_serve_path.get()
        if not os.path.isdir(current_serve_dir): messagebox.showerror("Error", "Serve from Path must be a valid directory first."); return
        initial_subpath_abs = os.path.join(current_serve_dir, self.update_web_subpath_internal)
        if not os.path.isdir(initial_subpath_abs): initial_subpath_abs = current_serve_dir
        dirname = filedialog.askdirectory(title="Select Update Subdirectory (within Serve Path)", initialdir=initial_subpath_abs)
        if dirname:
            if os.path.commonpath([current_serve_dir, dirname]) == os.path.normpath(current_serve_dir):
                relative_path = os.path.relpath(dirname, current_serve_dir)
                if relative_path == ".": relative_path = ""
                self.update_web_subpath_internal = relative_path
                self.update_web_subpath_display.set("/" + relative_path if relative_path else "/")
            else: messagebox.showwarning("Invalid Subpath", "Selected directory must be within the 'Serve from Path'.")
    def select_scan_source_path(self): # ... (no changes)
        if self.httpd: messagebox.showwarning("Server Running", "Cannot change scan source."); return
        initial = self.scan_source_path.get() or os.path.expanduser("~")
        dirname = filedialog.askdirectory(initialdir=initial, title="Select Source Folder for New Files")
        if dirname: self.scan_source_path.set(dirname)
    def add_log_message(self, message): # ... (no changes)
        if not self.master or not self.log_text.winfo_exists(): return
        self.log_text.config(state=tk.NORMAL)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
    def set_controls_state(self, is_running):
        control_state = tk.DISABLED if is_running else tk.NORMAL
        self.path_entry.config(state=control_state); self.select_path_button.config(state=control_state)
        self.port_entry.config(state=control_state)
        self.update_subpath_entry.config(state=control_state); self.select_update_subpath_button.config(state=control_state)
        self.scan_source_entry.config(state=control_state); self.select_scan_source_button.config(state=control_state)

        if hasattr(self, 'scan_type_frame'):
            for child in self.scan_type_frame.winfo_children():
                if isinstance(child, tk.Radiobutton):
                    child.config(state=control_state)

        self.map_web_path_entry.config(state=control_state); self.map_local_folder_entry.config(state=control_state); self.select_map_local_button.config(state=control_state)
        self.write_access_checkbox.config(state=control_state)

        # --- Control API field state ---
        self.api_fs_root_entry.config(state=control_state)
        self.select_api_fs_root_button.config(state=control_state)

        self.use_latest_checkbox.config(state=tk.DISABLED if is_running else tk.NORMAL)
        self.on_use_latest_toggled()
    def toggle_server(self): # ... (no changes)
        if self.httpd: self.stop_server()
        else: self.start_server()
    def update_clickable_url(self): # ... (no changes)
        if self.httpd:
            port = self.current_port.get()
            url_path = self.update_web_subpath_display.get().lstrip('/')
            if url_path and not url_path.endswith('/'): url_path += '/'
            full_url = f"http://localhost:{port}/{url_path}"
            self.server_url_var.set(full_url)
        else: self.server_url_var.set("")
    def start_server(self): # ... (no changes)
        try:
            port_str = self.current_port.get(); port = int(port_str)
            if not port_str.isdigit(): raise ValueError("Port must be a number.")
            if not (0 < port < 65536): raise ValueError("Port number out of range (1-65535).")
            serve_path_str = self.current_serve_path.get()
            if not os.path.isdir(serve_path_str): messagebox.showerror("Error", f"Serve path invalid: {serve_path_str}"); return
            map_web_prefix = self.map_web_path_prefix.get().strip(); map_local_fld = self.map_local_folder.get().strip()
            if map_web_prefix and map_local_fld and not os.path.isdir(map_local_fld): messagebox.showerror("Error", f"Mapped local folder '{map_local_fld}' invalid."); return
            if map_web_prefix and not map_web_prefix.startswith("/"): messagebox.showerror("Error", "Mapped Web Path must start with a '/' or be empty."); return

            # --- Check and log API status ---
            api_root_path = self.api_fs_root.get().strip()
            api_enabled = api_root_path and os.path.isdir(api_root_path)

            if self.is_scan_active():
                try: os.path.normpath(os.path.join(serve_path_str, self.update_web_subpath_internal))
                except Exception: messagebox.showerror("Error", f"Invalid 'Update Web Path': {self.update_web_subpath_display.get()}"); return
                self._perform_initial_scan()
            socketserver.TCPServer.allow_reuse_address = True
            self.httpd = socketserver.TCPServer(("", port), CustomHTTPRequestHandler)
            self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True); self.server_thread.start()
            self.update_status_message(f"Web server started on port {port}"); self.update_clickable_url()
            self.add_log_message(f"Server started on port {port}, serving from '{serve_path_str}'")
            if map_web_prefix and map_local_fld: self.add_log_message(f"MAP: Web path '{map_web_prefix}' mapped to local '{map_local_fld}'")
            if self.is_scan_active(): self.add_log_message(f"SCAN: Monitoring '{self.scan_source_path.get()}' for *.{self.scan_file_type.get()} to update '{self.update_web_subpath_display.get()}'")

            # --- Log API status ---
            if api_enabled:
                self.add_log_message(f"API: Fully enabled. Root filesystem at '{api_root_path}'.")
            else:
                self.add_log_message("API: Core enabled (Ping/Session). Filesystem API disabled (root path not set).")

            if self.write_access_enabled.get() and map_web_prefix:
                self.add_log_message(f"RAW POST: Write access enabled for mapped path '{map_web_prefix}'")

            self.server_button.config(text="Stop Server", bg="salmon"); self.set_controls_state(is_running=True)
            self._active_connections = 0; self.active_connections_var.set(0)
        except ValueError as e: messagebox.showerror("Error", f"Invalid input: {e}")
        except OSError as e: messagebox.showerror("Error", f"Could not start server: {e}"); self.httpd = None
        except Exception as e: messagebox.showerror("Error", f"Unexpected error: {e}"); self.httpd = None
    def stop_server(self): # ... (no changes)
        if self.httpd:
            self.update_status_message("Web server stopping..."); self.update_clickable_url()
            self.add_log_message("Stopping server...")
            self.httpd.shutdown(); self.httpd.server_close()
            if self.server_thread and self.server_thread.is_alive(): self.server_thread.join(timeout=2.0)
            if self.server_thread and self.server_thread.is_alive(): self.add_log_message("Warning: Server thread did not exit cleanly.")
            self.httpd = None; self.server_thread = None
            self.last_scanned_source_file_path = None; self.last_scanned_source_file_mtime = 0.0
            self.add_log_message("Server stopped."); self.update_status_message("Web server stopped")
            self.server_button.config(text="Start Server", bg="lightgreen"); self.set_controls_state(is_running=False)
    def on_closing(self): # ... (no changes)
        if self.httpd and messagebox.askokcancel("Quit", "Server is running. Stop and quit?"): self.stop_server()
        self.master.destroy()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Development Web Server with GUI.",
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     fromfile_prefix_chars='@')
    parser.add_argument("-servefolder", type=str, help="Path to serve files from")
    parser.add_argument("-defaultfile", type=str, help="Default file name")
    parser.add_argument("-updatepath", type=str, help="Relative web path for file updates (e.g. / or /latest)")
    parser.add_argument("-updatesource", type=str, help="Folder path to scan for new files")
    parser.add_argument("-scanfiletype", type=str, choices=['html', 'zip'], help="Type of file to scan for (html or zip)")
    parser.add_argument("-port", type=int, help="Port number")
    parser.add_argument("-uselatest", action="store_true", help="Check 'Use latest HTML' on launch.")
    parser.add_argument("-mapwebpath", type=str, help="Web path prefix to map (e.g. /images)")
    parser.add_argument("-maplocalfolder", type=str, help="Local folder to map the web path to")
    parser.add_argument("-fsroot", type=str, help="Path for the API Filesystem Root (enables API)") # <-- NEW CLI ARG
    parser.add_argument("-start", action="store_true", help="Start the server immediately.")

    cli_args = parser.parse_args()
    root = tk.Tk()
    app = WebServerApp(root, cli_args)
    root.mainloop()
