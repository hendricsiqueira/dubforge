from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, ClassVar

from .store import ProjectStore

EventCallback = Callable[[str, str, float | None], None]


class ProjectProgress:
    """Persistent exact counters plus an explicitly estimated weighted total."""

    DEFAULT_WEIGHTS: ClassVar[dict[str, float]] = {
        "preparation": 0.04,
        "separation": 0.10,
        "transcription": 0.11,
        "translation": 0.20,
        "dubbing": 0.50,
        "export": 0.05,
    }

    def __init__(self, store: ProjectStore, project: dict[str, Any], callback: EventCallback):
        self.store = store
        self.project = project
        self.callback = callback
        existing = project.get("progress", {})
        self.started_monotonic = time.monotonic()
        self.last_save = 0.0
        self.weights = self._weights(project)
        # Each execution gets fresh counters. Cached artifacts are discovered
        # immediately and marked complete again. This keeps a resumed project
        # with newly added languages from displaying 100% too early.
        self.data = {
            "stage": "queue",
            "message": "Aguardando",
            "stage_progress": 0.0,
            "overall_progress": 0.0,
            "overall_is_estimate": True,
            "current_language": None,
            "items_done": 0,
            "items_total": 0,
            "elapsed_seconds": 0.0,
            "eta_seconds": None,
            "stages": {},
            "run_number": int(existing.get("run_number", 0)) + 1,
        }
        self.project["progress"] = self.data
        self.store.save(self.project)

    def _weights(self, project: dict[str, Any]) -> dict[str, float]:
        weights = dict(self.DEFAULT_WEIGHTS)
        settings = project.get("settings", {})
        if not settings.get("preserve_background", True):
            removed = weights.pop("separation")
            weights["dubbing"] += removed
        if not settings.get("generate_srt", True):
            weights["translation"] -= 0.05
            weights["dubbing"] += 0.05
        total = sum(weights.values()) or 1.0
        return {key: value / total for key, value in weights.items()}

    def set(
        self,
        stage: str,
        message: str,
        done: float = 0.0,
        total: float = 1.0,
        *,
        language: str | None = None,
        force: bool = False,
    ) -> None:
        progress = 1.0 if total <= 0 else min(1.0, max(0.0, done / total))
        stages = self.data["stages"]
        stage_data = stages.setdefault(stage, {})
        stage_data.update({
            "done": done,
            "total": total,
            "progress": progress,
            "message": message,
            "updated_at_monotonic": time.monotonic(),
        })
        self.data.update({
            "stage": stage,
            "message": message,
            "stage_progress": progress,
            "current_language": language,
            "items_done": int(done),
            "items_total": int(total),
            "elapsed_seconds": round(time.monotonic() - self.started_monotonic, 1),
        })
        self.data["overall_progress"] = self._overall()
        self.data["eta_seconds"] = self._eta()
        self.project["progress"] = self.data
        now = time.monotonic()
        if force or progress >= 1.0 or now - self.last_save >= 0.5:
            self.store.save(self.project)
            self.last_save = now
        self.callback(stage, message, progress)

    def complete(self, stage: str, message: str | None = None) -> None:
        current = self.data["stages"].get(stage, {})
        total = float(current.get("total", 1.0))
        self.set(stage, message or current.get("message", stage), total, total, force=True)

    def finish(self) -> None:
        for stage in self.weights:
            if stage == "separation" and not self.project.get("settings", {}).get("preserve_background", True):
                continue
            current = self.data["stages"].setdefault(stage, {"total": 1.0})
            current.update({"done": current.get("total", 1.0), "progress": 1.0})
        self.data.update({
            "stage": "done",
            "message": "Processamento concluído",
            "stage_progress": 1.0,
            "overall_progress": 1.0,
            "eta_seconds": 0,
            "current_language": None,
            "elapsed_seconds": round(time.monotonic() - self.started_monotonic, 1),
        })
        self.store.save(self.project)
        self.callback("done", "Processamento concluído", 1.0)

    def _overall(self) -> float:
        value = 0.0
        for stage, weight in self.weights.items():
            if stage == "separation" and not self.project.get("settings", {}).get("preserve_background", True):
                value += weight
                continue
            progress = float(self.data["stages"].get(stage, {}).get("progress", 0.0))
            value += weight * min(1.0, max(0.0, progress))
        previous = float(self.data.get("overall_progress", 0.0))
        return round(max(previous, min(0.999, value)), 4)

    def _eta(self) -> int | None:
        overall = float(self.data.get("overall_progress", 0.0))
        elapsed = max(0.0, time.monotonic() - self.started_monotonic)
        if overall < 0.01 or elapsed < 3:
            return None
        return max(0, round(elapsed / overall * (1.0 - overall)))
