"""
System Telemetry Service
========================
Real-time resource telemetry for CPU, Memory, GPU / VRAM, and Disk I/O.
Used by the Studio Status Bar centered telemetry HUD.
"""

import os
import sys
import time
import psutil

# Cache process reference
_PROC = psutil.Process()
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Optional NVML for GPU load
_NVML_INITED = False
_NVML_HANDLE = None
try:
    import pynvml
    pynvml.nvmlInit()
    _NVML_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
    _NVML_INITED = True
except Exception:
    pass

_last_io_time = 0.0
_last_io_read_bytes = 0
_last_io_write_bytes = 0
_last_read_rate_mb = 0.0
_last_write_rate_mb = 0.0


def get_system_telemetry() -> dict:
    global _last_io_time, _last_io_read_bytes, _last_io_write_bytes, _last_read_rate_mb, _last_write_rate_mb

    # 1. CPU
    try:
        cpu_pct = round(psutil.cpu_percent(interval=None), 1)
        app_cpu_pct = round(_PROC.cpu_percent(interval=None), 1)
        cpu_count = psutil.cpu_count(logical=True) or 1
    except Exception:
        cpu_pct, app_cpu_pct, cpu_count = 0.0, 0.0, 1

    # 2. RAM
    try:
        mem = psutil.virtual_memory()
        ram_pct = round(mem.percent, 1)
        ram_used_gb = round(mem.used / (1024**3), 1)
        ram_total_gb = round(mem.total / (1024**3), 1)
        app_ram_gb = round(_PROC.memory_info().rss / (1024**3), 2)
    except Exception:
        ram_pct, ram_used_gb, ram_total_gb, app_ram_gb = 0.0, 0.0, 0.0, 0.0

    # 3. GPU & VRAM
    gpu_util = 0
    gpu_name = "GPU"
    vram_used_gb = 0.0
    vram_total_gb = 0.0
    vram_free_gb = 0.0
    vram_pct = 0.0

    if _NVML_INITED and _NVML_HANDLE is not None:
        try:
            import pynvml
            rates = pynvml.nvmlDeviceGetUtilizationRates(_NVML_HANDLE)
            gpu_util = int(rates.gpu)
        except Exception:
            pass

    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            free_b, total_b = torch.cuda.mem_get_info()
            used_b = total_b - free_b
            vram_free_gb = round(free_b / (1024**3), 2)
            vram_total_gb = round(total_b / (1024**3), 2)
            vram_used_gb = round(used_b / (1024**3), 2)
            vram_pct = round((used_b / max(1, total_b)) * 100, 1)
    except Exception:
        pass

    # 4. DISK & I/O
    disk_drive = os.path.splitdrive(_BASE_DIR)[0] or "C:"
    disk_pct = 0.0
    disk_free_gb = 0.0
    disk_total_gb = 0.0
    try:
        disk = psutil.disk_usage(_BASE_DIR)
        disk_pct = round(disk.percent, 1)
        disk_free_gb = round(disk.free / (1024**3), 1)
        disk_total_gb = round(disk.total / (1024**3), 1)
    except Exception:
        pass

    # Process disk I/O rates
    now = time.time()
    try:
        io = _PROC.io_counters()
        cur_read = io.read_bytes
        cur_write = io.write_bytes
        if _last_io_time > 0 and (now - _last_io_time) > 0.4:
            dt = now - _last_io_time
            _last_read_rate_mb = round((cur_read - _last_io_read_bytes) / (1024**2 * dt), 1)
            _last_write_rate_mb = round((cur_write - _last_io_write_bytes) / (1024**2 * dt), 1)
        _last_io_time = now
        _last_io_read_bytes = cur_read
        _last_io_write_bytes = cur_write
    except Exception:
        pass

    return {
        "cpu_pct": cpu_pct,
        "app_cpu_pct": app_cpu_pct,
        "cpu_count": cpu_count,
        "ram_pct": ram_pct,
        "ram_used_gb": ram_used_gb,
        "ram_total_gb": ram_total_gb,
        "app_ram_gb": app_ram_gb,
        "gpu_util": gpu_util,
        "gpu_name": gpu_name,
        "vram_pct": vram_pct,
        "vram_used_gb": vram_used_gb,
        "vram_free_gb": vram_free_gb,
        "vram_total_gb": vram_total_gb,
        "disk_drive": disk_drive,
        "disk_pct": disk_pct,
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
        "io_read_mb_s": max(0.0, _last_read_rate_mb),
        "io_write_mb_s": max(0.0, _last_write_rate_mb),
    }
