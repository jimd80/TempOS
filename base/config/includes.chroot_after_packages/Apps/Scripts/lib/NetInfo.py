import re
import time
from urllib.parse import urlparse
from lib.utils import run_cmd

class NetInfo:
    def __init__(self):
        self.status = 0
        self.status_text = "Internet disabled"
        self.redirect_url = None
        self.portal_type = None
        self.check()

    def check(self):
        cmd = "curl -s -I -m 3 http://google.be/"
        out = run_cmd(cmd)
        self.redirect_url = None
        self.portal_type = None

        if not out:
            self.status = 0
            self.status_text = "No Internet Connection"
            return

        lines = out.splitlines()
        if not lines:
            self.status = 0
            return

        # Extract HTTP status code
        try:
            first_line = lines[0]
            parts = first_line.split()
            code = int(parts[1]) if len(parts) > 1 else 0
        except Exception:
            self.status = 0
            return

        # Extract redirect location if present
        for line in lines:
            if line.lower().startswith("location:"):
                self.redirect_url = line.split(":", 1)[1].strip()
                break

        if code in [200, 301]:
            self.status = 2
            self.status_text = "Full Internet Access"
        elif code in [302, 303, 307]:
            self.status = 1
            self.status_text = "Behind Captive Portal"
            self.portal_type = "Unknown"
            if self.redirect_url:
                if "securelogin.hpe.com" in self.redirect_url:
                    self.portal_type = "HPE"
                elif "guest_tou" in self.redirect_url:
                    self.portal_type = "Ruckus"
        else:
            self.status = 0
            self.status_text = f"Connection Error ({code})"

    def unlock_portal(self):
        if self.status != 1:
            return

        # --- Portal Type 1: HPE Aruba Swarm ---
        if self.portal_type == "HPE":
            cmd = (
                "curl -m 10 'https://securelogin.hpe.com/swarm.cgi' -s --include --insecure "
                "-H 'Content-Type: application/x-www-form-urlencoded' "
                "-H 'User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0' "
                "--data-raw 'orig_url=68747470733a2f2f676f6f676c652e62652f&opcode=cp_ack'"
            )
            run_cmd(cmd)

        # --- Portal Type 2: Ruckus / GoAhead Guest ToU ---
        elif self.portal_type == "Ruckus":
            target_url = self.redirect_url or ""
            parsed = urlparse(target_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            form_url = f"{base_url}/forms/guest_toued"

            cmd = (
                f"curl -m 10 '{form_url}' -s --include "
                f"-H 'Content-Type: application/x-www-form-urlencoded' "
                f"-H 'Origin: {base_url}' "
                f"-H 'Referer: {target_url}' "
                f"--data-raw 'origurl=http%3A%2F%2Fgoogle.be%2F&ok='"
            )
            run_cmd(cmd)

        else:
            # unknown portal, user should manually unlock
            pass

        time.sleep(1)
        self.check()

    def __str__(self):
        lines = [f"Internet Status: {self.status} ({self.status_text})"]
        if self.portal_type is not None:
            lines.append(f"Portal type: {self.portal_type}")
            r_url = (self.redirect_url or "")[:80]
            lines.append(f"Portal URL: {r_url}")
        return "\n".join(lines)