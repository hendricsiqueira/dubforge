from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

GIB = 1024 ** 3


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class ResourceSnapshot:
    captured_at: float
    disk_total_gb: float
    disk_free_gb: float
    ram_total_gb: float
    ram_available_gb: float
    gpu_name: str | None = None
    gpu_vram_total_gb: float | None = None
    gpu_vram_used_gb: float | None = None
    gpu_vram_free_gb: float | None = None
    gpu_utilization: float | None = None
    gpu_temperature: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResourceMonitor:
    """Portable, failure-tolerant resource sampling for long-running jobs."""

    def __init__(self, storage_root: str | Path):
        self.storage_root = Path(storage_root).resolve()
        self.warning_disk_gb = _float_env("DUBFORGE_DISK_WARNING_GB", 20.0)
        self.minimum_start_disk_gb = _float_env("DUBFORGE_MIN_START_DISK_GB", 10.0)
        self.minimum_runtime_disk_gb = _float_env("DUBFORGE_MIN_RUNTIME_DISK_GB", 5.0)
        self.resume_disk_gb = max(
            self.minimum_runtime_disk_gb + 1.0,
            _float_env("DUBFORGE_RESUME_DISK_GB", 7.0),
        )

    def sample(self) -> ResourceSnapshot:
        disk = shutil.disk_usage(self.storage_root)
        ram_total, ram_available = self._ram()
        gpu = self._gpu()
        return ResourceSnapshot(
            captured_at=time.time(),
            disk_total_gb=round(disk.total / GIB, 2),
            disk_free_gb=round(disk.free / GIB, 2),
            ram_total_gb=round(ram_total / GIB, 2),
            ram_available_gb=round(ram_available / GIB, 2),
            **gpu,
        )

    @staticmethod
    def _ram() -> tuple[int, int]:
        try:
            import psutil

            vm = psutil.virtual_memory()
            return int(vm.total), int(vm.available)
        except Exception:
            pass
        try:
            values: dict[str, int] = {}
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    key, value = line.split(":", 1)
                    values[key] = int(value.strip().split()[0]) * 1024
            return values.get("MemTotal", 0), values.get("MemAvailable", values.get("MemFree", 0))
        except Exception:
            return 0, 0

    @staticmethod
    def _gpu() -> dict[str, Any]:
        executable = shutil.which("nvidia-smi")
        if not executable:
            return {}
        fields = (
            "name,memory.total,memory.used,memory.free,"
            "utilization.gpu,temperature.gpu"
        )
        try:
            result = subprocess.run(
                [
                    executable,
                    f"--query-gpu={fields}",
                    "--format=csv,noheader,nounits",
                    "--id=0",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            parts = [part.strip() for part in result.stdout.splitlines()[0].split(",")]
            if len(parts) != 6:
                return {}
            total, used, free, utilization, temperature = map(float, parts[1:])
            return {
                "gpu_name": parts[0],
                "gpu_vram_total_gb": round(total / 1024, 2),
                "gpu_vram_used_gb": round(used / 1024, 2),
                "gpu_vram_free_gb": round(free / 1024, 2),
                "gpu_utilization": round(utilization, 1),
                "gpu_temperature": round(temperature, 1),
            }
        except Exception:
            return {}

    def can_start(self, snapshot: ResourceSnapshot | None = None) -> bool:
        snapshot = snapshot or self.sample()
        return snapshot.disk_free_gb >= self.minimum_start_disk_gb

    def runtime_disk_ok(self, snapshot: ResourceSnapshot | None = None) -> bool:
        snapshot = snapshot or self.sample()
        return snapshot.disk_free_gb >= self.minimum_runtime_disk_gb

    def disk_recovered(self, snapshot: ResourceSnapshot | None = None) -> bool:
        snapshot = snapshot or self.sample()
        return snapshot.disk_free_gb >= self.resume_disk_gb


class AdaptiveScheduler:
    """Select conservative concurrency from live resources and user policy."""

    MODES = ("Automático", "Econômico", "Equilibrado", "Máximo")

    def __init__(self, mode: str = "Automático"):
        self.mode = mode if mode in self.MODES else "Automático"
        self.voxcpm_worker_gb = _float_env("DUBFORGE_VOXCPM_WORKER_GB", 11.0)
        self.vram_reserve_gb = _float_env("DUBFORGE_VRAM_RESERVE_GB", 4.0)

    def tts_workers(self, snapshot: ResourceSnapshot) -> int:
        if self.mode == "Econômico":
            return 1
        if snapshot.gpu_vram_free_gb is None:
            return 1
        possible = int(max(0.0, snapshot.gpu_vram_free_gb - self.vram_reserve_gb) // self.voxcpm_worker_gb)
        limit = 3 if self.mode in {"Automático", "Máximo"} else 2
        if self.mode == "Máximo" and possible == 0:
            possible = 1
        return max(1, min(limit, possible))

    def translation_batch_size(self, snapshot: ResourceSnapshot) -> int:
        total = snapshot.gpu_vram_total_gb or 0.0
        if self.mode == "Econômico":
            return 4
        if total >= 30:
            return 16 if self.mode in {"Automático", "Máximo"} else 12
        if total >= 20:
            return 12 if self.mode == "Máximo" else 8
        return 8 if total >= 10 else 4

    def decision(self, snapshot: ResourceSnapshot) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "tts_workers": self.tts_workers(snapshot),
            "translation_batch_size": self.translation_batch_size(snapshot),
            "voxcpm_worker_estimate_gb": self.voxcpm_worker_gb,
            "vram_reserve_gb": self.vram_reserve_gb,
        }
