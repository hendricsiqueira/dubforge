import tempfile
import unittest
from pathlib import Path

from dubforge.progress import ProjectProgress
from dubforge.store import ProjectStore


class ProgressTests(unittest.TestCase):
    def test_progress_persists_exact_counters_and_monotonic_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "audio.wav"
            source.write_bytes(b"audio")
            store = ProjectStore(root / "projects")
            project = store.create("Teste", source, {
                "preserve_background": True,
                "generate_srt": True,
            })
            events = []
            progress = ProjectProgress(store, project, lambda *event: events.append(event))
            progress.set("preparation", "Preparando", 1, 2, force=True)
            first = project["progress"]["overall_progress"]
            progress.set("preparation", "Preparando", 2, 2, force=True)
            second = project["progress"]["overall_progress"]

            loaded = store.get(project["id"])["progress"]
            self.assertEqual(loaded["stages"]["preparation"]["done"], 2)
            self.assertGreaterEqual(second, first)
            self.assertTrue(events)

    def test_new_run_resets_stale_completed_percentage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "audio.wav"
            source.write_bytes(b"audio")
            store = ProjectStore(root / "projects")
            project = store.create("Teste", source, {
                "preserve_background": False,
                "generate_srt": True,
            })
            project["progress"] = {
                "overall_progress": 1.0,
                "elapsed_seconds": 500,
                "run_number": 2,
                "stages": {"dubbing": {"progress": 1.0}},
            }
            store.save(project)

            progress = ProjectProgress(store, project, lambda *args: None)

            self.assertEqual(progress.data["overall_progress"], 0.0)
            self.assertEqual(progress.data["run_number"], 3)
            self.assertEqual(progress.data["stages"], {})


if __name__ == "__main__":
    unittest.main()
