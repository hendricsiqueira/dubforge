import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dubforge.jobs import JobManager, JobStore
from dubforge.store import BatchStore, ProjectStore


class FakePipeline:
    def __init__(self, store, zast_path):
        self.store = store

    def run(self, project_id, callback):
        project = self.store.get(project_id)
        project["progress"] = {
            "overall_progress": 0.5,
            "stage_progress": 0.5,
            "eta_seconds": 1,
            "items_done": 1,
            "items_total": 2,
            "current_language": "English",
        }
        self.store.save(project)
        callback("dubbing", "Gerando", 0.5)
        output = self.store.project_dir(project_id) / "outputs" / "en" / "result.mp3"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp3")
        project = self.store.get(project_id)
        project["progress"]["overall_progress"] = 1.0
        self.store.save(project)
        return [str(output)]


class JobTests(unittest.TestCase):
    def test_job_store_is_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = JobStore(Path(tmp))
            created = jobs.create("project", "demo", "Demo", "Automático")
            jobs.update(created["id"], status="running", message="Teste")
            reloaded = JobStore(Path(tmp)).get(created["id"])
            self.assertEqual(reloaded["status"], "running")
            self.assertEqual(reloaded["message"], "Teste")

    def test_manager_finishes_detached_job_and_keeps_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "audio.wav"
            source.write_bytes(b"audio")
            projects = ProjectStore(root / "projects")
            project = projects.create("Teste", source, {"adaptive_mode": "Econômico"})
            batches = BatchStore(root / "projects" / "batches")
            thresholds = {
                "DUBFORGE_MIN_START_DISK_GB": "0",
                "DUBFORGE_MIN_RUNTIME_DISK_GB": "0",
                "DUBFORGE_RESUME_DISK_GB": "1",
            }
            with patch.dict(os.environ, thresholds), patch("dubforge.jobs.DubPipeline", FakePipeline):
                manager = JobManager(projects, batches, root / "zast", root / "projects" / "jobs")
                job = manager.submit_project(project["id"], "Econômico")
                deadline = time.time() + 5
                while time.time() < deadline:
                    current = manager.jobs.get(job["id"])
                    if current["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.05)
                current = manager.jobs.get(job["id"])

            self.assertEqual(current["status"], "completed")
            self.assertEqual(current["overall_progress"], 1.0)
            self.assertTrue(current["outputs"][0].endswith("result.mp3"))

    def test_paused_job_can_resume_after_manager_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "audio.wav"
            source.write_bytes(b"audio")
            projects = ProjectStore(root / "projects")
            project = projects.create("Teste", source, {"adaptive_mode": "Econômico"})
            batches = BatchStore(root / "projects" / "batches")
            jobs_root = root / "projects" / "jobs"
            stored = JobStore(jobs_root).create("project", project["id"], "Teste", "Econômico")
            JobStore(jobs_root).update(stored["id"], status="paused")
            thresholds = {
                "DUBFORGE_MIN_START_DISK_GB": "0",
                "DUBFORGE_MIN_RUNTIME_DISK_GB": "0",
                "DUBFORGE_RESUME_DISK_GB": "1",
            }
            with patch.dict(os.environ, thresholds), patch("dubforge.jobs.DubPipeline", FakePipeline):
                manager = JobManager(projects, batches, root / "zast", jobs_root)
                self.assertEqual(manager.jobs.get(stored["id"])["status"], "paused")
                manager.resume(stored["id"])
                deadline = time.time() + 5
                while time.time() < deadline:
                    current = manager.jobs.get(stored["id"])
                    if current["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.05)

            self.assertEqual(current["status"], "completed")


if __name__ == "__main__":
    unittest.main()
