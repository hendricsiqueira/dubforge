from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .store import atomic_json_write
from .zast_bridge import ZastBridge

_BRIDGES: dict[tuple[str, str], ZastBridge] = {}


class WorkerCancelled(RuntimeError):
    pass


def _honor_control(task: dict[str, Any], progress_path: Path) -> None:
    control_value = task.get("control_path")
    if not control_value:
        return
    control_path = Path(control_value)
    while True:
        try:
            state = json.loads(control_path.read_text(encoding="utf-8")).get("state", "run")
        except (OSError, json.JSONDecodeError):
            state = "run"
        if state == "cancel":
            raise WorkerCancelled(f"Dublagem de {task['target_language']} cancelada")
        if state != "pause":
            return
        atomic_json_write(progress_path, {
            "pid": os.getpid(),
            "stage": "paused",
            "message": f"{task['target_language']} pausado",
            "progress": None,
            "language": task["target_language"],
        })
        time.sleep(0.5)


def _bridge(zast_path: str, project_dir: str) -> ZastBridge:
    key = (zast_path, project_dir)
    instance = _BRIDGES.get(key)
    if instance is None:
        instance = ZastBridge(zast_path, project_dir)
        _BRIDGES[key] = instance
    return instance


def synthesize_task(task: dict[str, Any]) -> dict[str, Any]:
    """Spawn-safe TTS entrypoint; one cached VoxCPM instance per worker process."""

    progress_path = Path(task["progress_path"])

    def callback(stage: str, message: str, progress: float | None) -> None:
        _honor_control(task, progress_path)
        atomic_json_write(progress_path, {
            "pid": os.getpid(),
            "stage": stage,
            "message": message,
            "progress": progress,
            "language": task["target_language"],
        })

    bridge = _bridge(task["zast_path"], task["project_dir"])
    result = bridge.synthesize_language(
        task["translated"],
        task["target_language"],
        task["duration"],
        task.get("voice_reference"),
        task.get("background_path"),
        task["never_cut"],
        task["bitrate"],
        task["source_stem"],
        callback,
    )
    result["language"] = task["target_language"]
    result["worker_pid"] = os.getpid()
    atomic_json_write(progress_path, {
        "pid": os.getpid(),
        "stage": "dubbing",
        "message": f"{task['target_language']} concluído",
        "progress": 1.0,
        "language": task["target_language"],
    })
    return result
