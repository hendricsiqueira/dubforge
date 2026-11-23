import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from dubforge.tts_worker import WorkerCancelled, _honor_control


class WorkerControlTests(unittest.TestCase):
    def test_worker_stops_when_job_is_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control = root / "control.json"
            control.write_text('{"state": "cancel"}', encoding="utf-8")
            task = {"control_path": str(control), "target_language": "English"}

            with self.assertRaises(WorkerCancelled):
                _honor_control(task, root / "progress.json")

    def test_worker_waits_while_job_is_paused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control = root / "control.json"
            progress = root / "progress.json"
            control.write_text('{"state": "pause"}', encoding="utf-8")
            task = {"control_path": str(control), "target_language": "English"}

            def resume() -> None:
                time.sleep(0.1)
                control.write_text('{"state": "run"}', encoding="utf-8")

            threading.Thread(target=resume, daemon=True).start()
            _honor_control(task, progress)

            self.assertEqual(json.loads(progress.read_text(encoding="utf-8"))["stage"], "paused")


if __name__ == "__main__":
    unittest.main()
