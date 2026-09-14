from libtempos.utils import run_cmd, to_mb

class MemInfo:
    def __init__(self):
        self.total_phys = 0
        self.avail_phys = 0
        self.used_proc = 0
        self.used_ramdisk = 0
        self.total_ramdisk = 0
        self.comp_swap_size = 0 
        self.uncomp_swap_size = 0 
        self.comp_ratio = 0
        self.est_total_virt = 0
        self.est_avail_virt = 0
        self._collect()

    def _collect(self):
        free_out = run_cmd("free -b").splitlines()
        if len(free_out) < 2: return
        mem_vals = free_out[1].split()
        total_ram = int(mem_vals[1])
        used_ram_reported = int(mem_vals[2]) 
        avail_ram = int(mem_vals[6]) 

        zram_out = run_cmd("zramctl -o DISKSIZE,DATA,COMPR,TOTAL --raw -b /dev/zram0", as_root=True)
        zram_data = 0
        zram_compr = 0
        zram_total_used = 0
        if zram_out:
            z_lines = zram_out.strip().splitlines()
            target_vals = []
            if len(z_lines) >= 2:
                target_vals = z_lines[1].split()
            elif len(z_lines) == 1 and z_lines[0].strip() and z_lines[0].split()[0].isdigit():
                target_vals = z_lines[0].split()
            if len(target_vals) >= 4:
                zram_data = int(target_vals[1])
                zram_compr = int(target_vals[2])
                zram_total_used = int(target_vals[3])
            
        df_out = run_cmd("df -t tmpfs -B1")
        used_tmpfs = 0
        total_tmpfs = 0
        for line in df_out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 6:
                mount = parts[5]
                total_size = int(parts[1])
                used_size = int(parts[2])
                if "/dev/shm" not in mount:
                    used_tmpfs += used_size
                    total_tmpfs += total_size

        total_used_virt = used_ram_reported - zram_compr + zram_data
        zram_overhead = zram_total_used - zram_compr 
        self.used_proc = total_used_virt - used_tmpfs + zram_overhead
        self.used_ramdisk = used_tmpfs
        self.total_ramdisk = total_tmpfs
        self.total_phys = total_ram
        self.avail_phys = avail_ram
        self.comp_swap_size = zram_compr
        self.uncomp_swap_size = zram_data
        
        if zram_data > 20000000:
            self.comp_ratio = int((zram_compr * 100) / zram_data)
        else:
            self.comp_ratio = 30 

        free_ram = total_ram - used_ram_reported
        uncompressable_est = 500 * 1024 * 1024 
        compressable_ram = used_ram_reported - zram_total_used - uncompressable_est
        if compressable_ram < 0: compressable_ram = 0
        
        freeable = compressable_ram - (compressable_ram * self.comp_ratio // 100)
        estimated_free_phys = freeable + free_ram
        estimated_free_virt = (estimated_free_phys * 100) // self.comp_ratio if self.comp_ratio > 0 else estimated_free_phys
        self.est_avail_virt = estimated_free_virt
        self.est_total_virt = self.est_avail_virt + self.used_proc + self.used_ramdisk

    def get_report(self):
        lines = []
        lines.append(f"{to_mb(self.total_phys)} MB Total physical RAM")
        lines.append(f"{to_mb(self.avail_phys)} MB Available physical RAM (includes buff/cache)")
        lines.append(f"{to_mb(self.used_proc)} MB used by processes (including 'swap' which is compressed)")
        lines.append(f"{to_mb(self.used_ramdisk)} MB used by RAM disks (of total capacity {to_mb(self.total_ramdisk)} MB)")
        lines.append(f"{to_mb(self.uncomp_swap_size)} MB of memory (=swap) is compressed into {to_mb(self.comp_swap_size)}MB ({self.comp_ratio}% compression ratio)")
        lines.append(f"{to_mb(self.est_total_virt)} MB Estimated total virtual memory (assuming current compression ratio and 500MB uncompressable)")
        lines.append(f"{to_mb(self.est_avail_virt)} MB Estimated available virtual memory")
        return "\n".join(lines)
