from __future__ import annotations

import json
import queue
import threading
import uuid
from pathlib import Path
from typing import Any

from .pipeline import DubPipeline, PipelineInterrupted
from .resources import AdaptiveScheduler, ResourceMonitor
from .store import BatchStore, ProjectStore, atomic_json_write, utc_now

ACTIVE_STATUSES = {
    "pending", "running", "pause_requested", "paused", "waiting_for_disk", "cancelling"
}
FINAL_STATUSES = {"completed", "failed", "cancelled"}


class JobCancelled(PipelineInterrupted):
    pass


class JobStore:
    """Atomic persistent records used by both the worker and reconnecting UIs."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(self, kind: str, target_id: str, name: str, mode: str) -> dict[str, Any]:
        now = utc_now()
        job = {
            "schema_version": 1,
            "id": uuid.uuid4().hex,
            "kind": kind,
            "target_id": target_id,
            "name": name,
            "mode": mode,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "heartbeat_at": None,
            "stage": "queue",
            "message": "Aguardando na fila",
            "stage_progress": 0.0,
            "overall_progress": 0.0,
            "eta_seconds": None,
            "current_project_id": target_id if kind == "project" else None,
            "current_language": None,
            "items_done": 0,
            "items_total": 0,
            "outputs": [],
            "resource": {},
            "adaptive": {},
            "last_error": None,
        }
        self.save(job)
        return job

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            path = self._path(job_id)
            if not path.exists():
                raise FileNotFoundError(f"Trabalho não encontrado: {job_id}")
            return json.loads(path.read_text(encoding="utf-8"))

    def save(self, job: dict[str, Any]) -> None:
        with self._lock:
            job["updated_at"] = utc_now()
            atomic_json_write(self._path(job["id"]), job)

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            job.update(changes)
            self.save(job)
            return job

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = []
            for path in self.root.glob("*.json"):
                try:
                    jobs.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
            return sorted(jobs, key=lambda item: item.get("updated_at", ""), reverse=True)

    def latest(self, active_first: bool = True) -> dict[str, Any] | None:
        jobs = self.list()
        if active_first:
            active = next((job for job in jobs if job.get("status") in ACTIVE_STATUSES), None)
            if active:
                return active
        return jobs[0] if jobs else None

    def _path(self, job_id: str) -> Path:
        safe = "".join(char for char in job_id if char.isalnum())
        if not safe:
            raise ValueError("Identificador de trabalho inválido")
        return self.root / f"{safe}.json"


class JobManager:
    """Process-owned queue detached from a browser/Gradio request lifecycle."""

    def __init__(
        self,
        projects: ProjectStore,
        batches: BatchStore,
        zast_path: str | Path,
        jobs_root: str | Path,
    ):
        self.projects = projects
        self.batches = batches
        self.zast_path = Path(zast_path).resolve()
        self.jobs = JobStore(jobs_root)
        self.resources = ResourceMonitor(projects.root)
        self._queue: queue.Queue[str] = queue.Queue()
        self._queued: set[str] = set()
        self._queue_lock = threading.Lock()
        self._wake = threading.Condition()
        self._stop = threading.Event()
        self._active_job_id: str | None = None
        self._recover_jobs()
        self._worker = threading.Thread(target=self._worker_loop, name="dubforge-job-worker", daemon=True)
        self._monitor = threading.Thread(target=self._monitor_loop, name="dubforge-resource-monitor", daemon=True)
        self._worker.start()
        self._monitor.start()

    def submit_project(self, project_id: str, mode: str = "Automático") -> dict[str, Any]:
        project = self.projects.get(project_id)
        existing = self._find_active("project", project_id)
        if existing:
            return existing
        job = self.jobs.create("project", project_id, project["name"], mode)
        self._enqueue(job["id"])
        return job

    def submit_batch(self, batch_id: str, mode: str = "Automático") -> dict[str, Any]:
        batch = self.batches.get(batch_id)
        existing = self._find_active("batch", batch_id)
        if existing:
            return existing
        job = self.jobs.create("batch", batch_id, batch["name"], mode)
        self._enqueue(job["id"])
        return job

    def pause(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job["status"] in {"pending", "running", "waiting_for_disk"}:
            job = self.jobs.update(job_id, status="pause_requested", message="Pausa solicitada")
            self._write_worker_control(job, "pause")
        return job

    def resume(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job["status"] in {"pause_requested", "paused", "waiting_for_disk"}:
            if self._active_job_id == job_id:
                job = self.jobs.update(job_id, status="running", message="Retomando processamento")
                self._write_worker_control(job, "run")
                with self._wake:
                    self._wake.notify_all()
            else:
                job = self.jobs.update(job_id, status="pending", message="Recolocado na fila para retomar")
                self._write_worker_control(job, "run")
                self._enqueue(job_id)
        elif job["status"] in {"failed", "cancelled"}:
            job = self.jobs.update(
                job_id,
                status="pending",
                message="Recolocado na fila para retomar",
                last_error=None,
                finished_at=None,
            )
            self._write_worker_control(job, "run")
            self._enqueue(job_id)
        return job

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job["status"] not in FINAL_STATUSES:
            job = self.jobs.update(job_id, status="cancelling", message="Cancelando após o item atual")
            self._write_worker_control(job, "cancel")
            with self._wake:
                self._wake.notify_all()
        return job

    def latest(self) -> dict[str, Any] | None:
        return self.jobs.latest()

    def _find_active(self, kind: str, target_id: str) -> dict[str, Any] | None:
        return next(
            (
                job for job in self.jobs.list()
                if job.get("kind") == kind
                and job.get("target_id") == target_id
                and job.get("status") in ACTIVE_STATUSES
            ),
            None,
        )

    def _recover_jobs(self) -> None:
        for job in reversed(self.jobs.list()):
            if job.get("status") == "cancelling":
                job["status"] = "cancelled"
                job["message"] = "Cancelamento concluído após reinício"
                job["finished_at"] = utc_now()
                self.jobs.save(job)
                continue
            if job.get("status") == "pause_requested":
                job["status"] = "paused"
                job["message"] = "Pausado durante a reinicialização"
                self.jobs.save(job)
                continue
            if job.get("status") in {"running", "waiting_for_disk"}:
                job["status"] = "pending"
                job["message"] = "Recuperado após reinício; aguardando retomada"
                job["stage"] = "recovery"
                self.jobs.save(job)
            if job.get("status") == "pending":
                self._enqueue(job["id"])

    def _enqueue(self, job_id: str) -> None:
        with self._queue_lock:
            if job_id in self._queued:
                return
            self._queued.add(job_id)
            self._queue.put(job_id)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            with self._queue_lock:
                self._queued.discard(job_id)
            self._active_job_id = job_id
            try:
                self._execute(job_id)
            finally:
                self._active_job_id = None
                self._queue.task_done()

    def _execute(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job["status"] == "cancelling":
            self.jobs.update(job_id, status="cancelled", finished_at=utc_now(), message="Cancelado")
            return
        if job["status"] in {"pause_requested", "paused"}:
            self._control_point(job_id, check_disk=False)
            job = self.jobs.get(job_id)
        started = job.get("started_at") or utc_now()
        self.jobs.update(
            job_id,
            status="running",
            started_at=started,
            finished_at=None,
            heartbeat_at=utc_now(),
            message="Iniciando processamento",
            last_error=None,
        )
        try:
            self._wait_for_start_space(job_id)
            if job["kind"] == "batch":
                outputs = self._execute_batch(job_id, job["target_id"])
            else:
                outputs = self._execute_project(job_id, job["target_id"])
            self.jobs.update(
                job_id,
                status="completed",
                stage="done",
                message="Processamento concluído",
                stage_progress=1.0,
                overall_progress=1.0,
                eta_seconds=0,
                outputs=sorted(set(outputs)),
                finished_at=utc_now(),
                heartbeat_at=utc_now(),
            )
            self._write_worker_control(self.jobs.get(job_id), "completed")
        except JobCancelled:
            cancelled = self.jobs.update(
                job_id,
                status="cancelled",
                message="Cancelado; os checkpoints concluídos foram preservados",
                finished_at=utc_now(),
                heartbeat_at=utc_now(),
            )
            self._write_worker_control(cancelled, "cancel")
        except Exception as exc:
            self.jobs.update(
                job_id,
                status="failed",
                message=f"Erro: {exc}",
                last_error=str(exc),
                finished_at=utc_now(),
                heartbeat_at=utc_now(),
            )

    def _execute_project(self, job_id: str, project_id: str) -> list[str]:
        job = self.jobs.update(job_id, current_project_id=project_id)
        self._write_worker_control(job, "run")

        def callback(stage: str, message: str, progress: float | None) -> None:
            self._control_point(job_id)
            project = self.projects.get(project_id)
            details = project.get("progress", {})
            job = self.jobs.get(job_id)
            overall = float(details.get("overall_progress", 0.0))
            if job.get("kind") == "batch":
                try:
                    batch = self.batches.get(job["target_id"])
                    values = [
                        float(self.projects.get(item_id).get("progress", {}).get("overall_progress", 0.0))
                        for item_id in batch["project_ids"]
                    ]
                    overall = sum(values) / max(1, len(values))
                except (FileNotFoundError, KeyError):
                    pass
            self.jobs.update(
                job_id,
                status="running",
                stage=stage,
                message=message,
                stage_progress=float(progress if progress is not None else details.get("stage_progress", 0.0)),
                overall_progress=overall,
                eta_seconds=details.get("eta_seconds"),
                current_language=details.get("current_language"),
                items_done=int(details.get("items_done", 0)),
                items_total=int(details.get("items_total", 0)),
                heartbeat_at=utc_now(),
            )

        project = self.projects.get(project_id)
        project.setdefault("settings", {})["adaptive_mode"] = self.jobs.get(job_id).get("mode", "Automático")
        snapshot = self.resources.sample()
        decision = AdaptiveScheduler(project["settings"]["adaptive_mode"]).decision(snapshot)
        project["runtime"] = {"resource": snapshot.as_dict(), "adaptive": decision}
        self.projects.save(project)
        self.jobs.update(job_id, resource=snapshot.as_dict(), adaptive=decision)
        return DubPipeline(self.projects, self.zast_path).run(project_id, callback)

    def _write_worker_control(self, job: dict[str, Any], state: str) -> None:
        project_id = job.get("current_project_id")
        if not project_id:
            return
        try:
            atomic_json_write(
                self.projects.project_dir(project_id) / "cache" / "job_control.json",
                {
                    "job_id": job["id"],
                    "state": state,
                    "updated_at": utc_now(),
                },
            )
        except OSError:
            # The main persistent status remains authoritative. A missing source
            # project will be reported normally by the pipeline.
            pass

    def _execute_batch(self, job_id: str, batch_id: str) -> list[str]:
        batch = self.batches.get(batch_id)
        batch["status"] = "running"
        batch["last_error"] = None
        self.batches.save(batch)
        outputs: list[str] = []
        total = len(batch["project_ids"])
        try:
            for index, project_id in enumerate(batch["project_ids"], 1):
                self._control_point(job_id)
                batch["current_project_id"] = project_id
                self.batches.save(batch)
                self.jobs.update(
                    job_id,
                    current_project_id=project_id,
                    items_done=index - 1,
                    items_total=total,
                    message=f"Arquivo {index}/{total}",
                )
                project_outputs = self._execute_project(job_id, project_id)
                outputs.extend(project_outputs)
                self._update_batch_progress(job_id, batch, index, total)
            batch["status"] = "completed"
            batch["current_project_id"] = None
            self.batches.save(batch)
            return outputs
        except Exception as exc:
            batch["status"] = "failed" if not isinstance(exc, JobCancelled) else "cancelled"
            batch["last_error"] = str(exc)
            self.batches.save(batch)
            raise

    def _update_batch_progress(self, job_id: str, batch: dict[str, Any], done: int, total: int) -> None:
        progresses = []
        for project_id in batch["project_ids"]:
            try:
                progresses.append(float(self.projects.get(project_id).get("progress", {}).get("overall_progress", 0.0)))
            except FileNotFoundError:
                progresses.append(0.0)
        overall = sum(progresses) / max(1, len(progresses))
        self.jobs.update(
            job_id,
            overall_progress=overall,
            items_done=done,
            items_total=total,
            message=f"{done}/{total} arquivos concluídos",
        )

    def _wait_for_start_space(self, job_id: str) -> None:
        while True:
            self._control_point(job_id, check_disk=False)
            snapshot = self.resources.sample()
            if self.resources.can_start(snapshot):
                return
            self.jobs.update(
                job_id,
                status="waiting_for_disk",
                stage="disk_guard",
                message=(
                    f"Aguardando espaço em disco: {snapshot.disk_free_gb:.1f} GB livres; "
                    f"mínimo para iniciar {self.resources.minimum_start_disk_gb:.1f} GB"
                ),
                resource=snapshot.as_dict(),
                heartbeat_at=utc_now(),
            )
            with self._wake:
                self._wake.wait(timeout=10.0)

    def _control_point(self, job_id: str, check_disk: bool = True) -> None:
        while True:
            job = self.jobs.get(job_id)
            if job["status"] == "cancelling":
                raise JobCancelled()
            if job["status"] in {"pause_requested", "paused"}:
                self.jobs.update(job_id, status="paused", message="Pausado após concluir o item atual")
                with self._wake:
                    self._wake.wait(timeout=2.0)
                continue
            if check_disk:
                snapshot = self.resources.sample()
                if not self.resources.runtime_disk_ok(snapshot):
                    self.jobs.update(
                        job_id,
                        status="waiting_for_disk",
                        stage="disk_guard",
                        message=(
                            f"Pausado para proteger o disco: {snapshot.disk_free_gb:.1f} GB livres. "
                            f"Libere espaço; retomada automática em {self.resources.resume_disk_gb:.1f} GB."
                        ),
                        resource=snapshot.as_dict(),
                        heartbeat_at=utc_now(),
                    )
                    while not self.resources.disk_recovered():
                        current = self.jobs.get(job_id)
                        if current["status"] == "cancelling":
                            raise JobCancelled()
                        with self._wake:
                            self._wake.wait(timeout=10.0)
                    self.jobs.update(job_id, status="running", message="Espaço recuperado; retomando")
            return

    def _monitor_loop(self) -> None:
        while not self._stop.wait(2.0):
            job_id = self._active_job_id
            if not job_id:
                continue
            try:
                snapshot = self.resources.sample()
                job = self.jobs.get(job_id)
                scheduler = AdaptiveScheduler(job.get("mode", "Automático"))
                decision = scheduler.decision(snapshot)
                self.jobs.update(
                    job_id,
                    resource=snapshot.as_dict(),
                    adaptive=decision,
                    heartbeat_at=utc_now(),
                )
            except Exception:
                continue
