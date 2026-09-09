* version history
** new in 13.6.0
- Added new wifi portal and show type in log
- Default Thonny config
- Updated webserver
- Added qwerty/azerty setting
- Added online update check on startup
- Added TurboWarp desktop icon
- Added installer when booted directely as .iso
- Added benchmark tool d2bench

** new in 13.5.0
- Wait for internet for infinite time, unless cancelled by user
- Replaced wait for internet time setting to offline mode to skip internet checking

** new in 13.4.0
- See tempos 13.4.0 changes
- Add Thonny (python editor)

** new in 13.3.1
- Ask destination folder on download in chromium
- Log startup sequence to start_log.txt
- Allow cancel auto close startup
- Clean old images on update
- Better update log
- Improved update screen
- Reboot button on update


**GPU / HDMI crash during presentations (X11/Intel Deadlocks):**
*   **Chromium Flags:** No longer use `--ignore-gpu-blocklist` and `--ignore-gpu-blacklist`. Instead, use `--enable-unsafe-swiftshader`. This ensures that if the GPU is blacklisted, Chromium will safely use CPU software emulation (SwiftShader) for WebGL (Scratch) instead of crashing the GPU.
*   **Debian Default Flags:** Removed `--enable-gpu-rasterization` from `/etc/chromium.d/default-flags`. Forcing this flag could conflict with the blacklist and force a buggy GPU to handle 2D drawing anyway, leading to X11 freezes under heavy load. On modern/supported GPUs, removing this makes no difference, as Chromium's internal logic will automatically enable hardware rasterization if it is safe to do so.
*   **Xorg Intel Driver:** Ran `apt purge xserver-xorg-video-intel`. This package contains legacy, unmaintained 2D drawing code that handles HDMI disconnects poorly. Purging it removes a problematic layer and forces Xorg to automatically fall back to the kernel's built-in `modesetting` driver, which handles hot-plugging and modern `i915` kernel graphics much more reliably.

**To consider if hardware freezes persist:**
*   Add boot parameter `i915.enable_psr=0`. This disables Panel Self Refresh (a complex power-saving technique where the GPU stops sending signals for static screen parts). Disabling it slightly increases laptop battery consumption, but drastically improves display stability on buggy Intel screens. It is safe to use and will be completely ignored by non-Intel (AMD/Nvidia) laptops.

**Remarks / Rejected Workarounds:**
*   Adding `drm_kms_helper.poll=0` (stopping the 10-second display polling) is **not recommended**. While it stops weak cables from causing software checks, it completely breaks "Plug and Play" monitor detection for older laptops and VGA ports.
*   Adding `video=HDMI-A-1:e` to the kernel boot parameters ('e' stands for force Enable/always assume connected) is **not recommended**. Port names vary wildly across different laptop brands (HDMI-A-1, DP-1, eDP-2), and forcing it creates an invisible "ghost monitor" on every laptop that boots the Live USB without a projector attached.
