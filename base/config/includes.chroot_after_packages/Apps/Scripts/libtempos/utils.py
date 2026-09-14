import os
import subprocess

def run_cmd(cmd, as_root=False, input_text=None):
    """Executes a shell command and returns stdout as string."""
    if as_root and os.geteuid() != 0:
        cmd = ["sudo"] + cmd if isinstance(cmd, list) else f"sudo {cmd}"
    
    try:
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        
        if isinstance(cmd, list):
            result = subprocess.run(cmd, input=input_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                                    text=True, env=env)
        else:
            result = subprocess.run(cmd, input=input_text, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                                    text=True, env=env)
        return result.stdout.strip()
    except Exception as e:
        return ""

def to_mb(bytes_val):
    try:
        return int(float(bytes_val) / (1024 * 1024))
    except (ValueError, TypeError):
        return 0