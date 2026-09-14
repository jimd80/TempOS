import os
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from libtempos.startup_logic import run_startup_sequence
from libtempos.update_logic import run_update_sequence
from libtempos.ops import perform_power_action

class StartupWindow(tk.Tk):
    def __init__(self, storage, settings):
        super().__init__()
        self.title("TempOS Startup")
        ws, hs = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 1000, 700
        self.geometry(f"{w}x{h}+{int((ws-w)/2)}+{int((hs-h)/2)}")
        
        self.storage = storage
        self.settings = settings
        self.auto_close_sec = 0
        self.auto_close_job = None
        self.available_update = None
        self.upd_seq = None
        self.update_has_error = False
        
        # Configure styles
        style = ttk.Style(self)
        style.configure("Green.TButton", foreground="green")
        style.map("Green.TButton", foreground=[('disabled', 'gray'), ('active', 'green'), ('!disabled', 'green')])

        self.log = scrolledtext.ScrolledText(self, wrap=tk.WORD, font=("Consolas", 10))
        self.log.pack(expand=True, fill='both', padx=5, pady=5)
        self.log.tag_config("ERROR", foreground="red")
        self.log.tag_config("WARNING", foreground="orange")
        self.log.tag_config("SUCCESS", foreground="green")
        self.log.tag_config("INFO", foreground="black")
        
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill='x', pady=5, padx=10)

        self.btn_subframe = ttk.Frame(btn_frame)
        self.btn_subframe.pack()

        self.btn = ttk.Button(self.btn_subframe, text="Close", command=self.on_btn_click, state='disabled')
        self.btn.pack(side='left', padx=5)

        self.btn_update = ttk.Button(self.btn_subframe, text="Download and install update", command=self.on_download_update, style="Green.TButton")
        # Not packed initially

        self.btn_reboot = ttk.Button(self.btn_subframe, text="Reboot System", command=self.on_reboot)
        # Not packed initially
        
        self.seq = None
        self.waiting_for_net = False
        self.skip_net = False
        self.has_error = False
        
        self.after(200, self.start_sequence)

    def start_sequence(self):
        self.log.insert(tk.END, "--- Starting Sequence ---\n", "INFO")
        self.seq = run_startup_sequence(self.storage, self.settings)
        self.process_step()

    def process_step(self):
        try:
            # If the user clicked "Skip waiting for internet", send SKIP back to generator
            action = "SKIP" if self.skip_net else None
            self.skip_net = False
            
            status, msg, replace = self.seq.send(action)
            
            if status == "UPDATE_AVAILABLE":
                self.available_update = getattr(self.storage, 'available_update', None)
                display_status = "INFO"
                tag = "SUCCESS"
                self.btn_update.pack(side='left', padx=5)
            else:
                # Translate WARN state for unified display logic
                display_status = "WARNING" if status in ["WAIT_NET_WARN", "WARNING"] else status
                if display_status == "ERROR":
                    self.has_error = True
                
                if display_status == "ERROR":
                    tag = "ERROR"
                elif display_status == "WARNING":
                    tag = "WARNING"
                elif display_status == "SUCCESS":
                    tag = "SUCCESS"
                else:
                    tag = "INFO"
            
            if replace:
                self.log.delete("end-2l", "end-1c")

            self.log.insert(tk.END, f"[{display_status}] {msg}\n", tag)
            self.log.see(tk.END)
            self.update()
            
            # Non-blocking delay for internet
            is_wait = status in ["WAIT_NET", "WAIT_NET_WARN"]
            if is_wait:
                self.waiting_for_net = True
                self.btn.configure(state='normal', text="Skip waiting for internet")
                self.after(1000, self.process_step)
                return
            else:
                self.waiting_for_net = False
                self.btn.configure(state='disabled', text="Close")
                
            self.after(10, self.process_step)
            
        except StopIteration:
            self.finish_sequence()

    def finish_sequence(self):
        self.log.insert(tk.END, "--- Finished ---\n", "INFO")

        # Save log to file
        try:
            if not os.path.exists(self.storage.config_folder):
                os.makedirs(self.storage.config_folder, exist_ok=True)
            
            log_path = os.path.join(self.storage.config_folder, "start_log.txt")
            with open(log_path, "w") as f:
                f.write(self.log.get("1.0", tk.END))
        except Exception as e:
            self.log.insert(tk.END, f"Failed to save log: {e}\n", "ERROR")

        self.btn.configure(state='normal', text="Close")
        
        # If there were no errors, initiate the auto-close sequence
        if not self.has_error and self.settings.close_startup_limit is not None:
            limit = int(self.settings.close_startup_limit)
            if self.available_update:
                limit = max(5, limit)
            self.auto_close_sec = limit
            self.do_auto_close()

    def do_auto_close(self):
        if self.auto_close_sec <= 0:
            self.destroy()
        else:
            self.btn.configure(text=f"Close ({self.auto_close_sec})")
            self.auto_close_sec -= 1
            self.auto_close_job = self.after(1000, self.do_auto_close)

    def on_download_update(self):
        if self.auto_close_job:
            self.after_cancel(self.auto_close_job)
            self.auto_close_job = None

        self.btn_update.pack_forget()
        self.btn.configure(state='disabled', text="Close")

        self.log.insert(tk.END, "\n--- Starting Update ---\n", "INFO")
        self.log.see(tk.END)

        options = {
            'item': self.available_update,
            'verify_src': False,
            'verify_dst': True,
            'use_settings': False,
            'update_apps': True,
            'copy_folders': False,
            'boot_default': True,
            'cleanup_old': True
        }

        self.update_has_error = False
        self.upd_seq = run_update_sequence(self.storage, options)
        self.process_update_step()

    def process_update_step(self):
        try:
            status, msg, replace = next(self.upd_seq)

            if status == "ERROR":
                self.update_has_error = True

            if replace:
                self.log.delete("end-2l", "end-1c")

            tag = status if status in ["ERROR", "WARNING", "SUCCESS", "INFO"] else "INFO"
            self.log.insert(tk.END, f"[{status}] {msg}\n", tag)
            self.log.see(tk.END)
            self.update()

            self.after(10, self.process_update_step)
        except StopIteration:
            self.finish_update()
        except Exception as e:
            self.log.insert(tk.END, f"[ERROR] Update failed: {e}\n", "ERROR")
            self.finish_update(success=False)

    def finish_update(self, success=True):
        self.log.insert(tk.END, "--- Update Finished ---\n", "INFO")
        self.log.see(tk.END)
        self.btn.configure(state='normal', text="Close")

        if success and not self.update_has_error:
            self.btn_reboot.pack(side='left', padx=5)

    def on_reboot(self):
        self.destroy()
        perform_power_action("reboot")

    def on_btn_click(self):
        if self.waiting_for_net:
            self.skip_net = True
            self.waiting_for_net = False
            self.btn.configure(state='disabled', text="Close")
        elif self.auto_close_job:
            self.after_cancel(self.auto_close_job)
            self.auto_close_job = None
            self.btn.configure(text="Close")
        else:
            self.destroy()