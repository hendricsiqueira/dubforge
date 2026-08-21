import json
import tempfile
import unittest
from pathlib import Path

from dubforge.store import BatchStore, ProjectStore, slugify


class StoreTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("Pregação 18/08/2026"), "pregacao-18-08-2026")

    def test_create_and_reload_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "video.mp4"
            source.write_bytes(b"demo")
            store = ProjectStore(tmp_path / "projects")
            settings = {"target_languages": ["English"]}
            project = store.create("Teste", source, settings)

            loaded = store.get(project["id"])
            self.assertEqual(loaded["name"], "Teste")
            self.assertEqual(Path(loaded["source"]["path"]).read_bytes(), b"demo")
            self.assertEqual(loaded["settings"], settings)

    def test_save_is_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "audio.wav"
            source.write_bytes(b"demo")
            store = ProjectStore(tmp_path / "projects")
            project = store.create("Audio", source, {})
            project["stages"]["transcription"] = "completed"
            store.save(project)
            json.loads((store.project_dir(project["id"]) / "project.json").read_text(encoding="utf-8"))

    def test_batch_persists_project_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ProjectStore(root / "projects")
            source = root / "audio.wav"
            source.write_bytes(b"demo")
            first = store.create("Primeiro", source, {})
            second = store.create("Segundo", source, {})
            batches = BatchStore(root / "projects" / "batches")

            batch = batches.create("Série Agosto", [first["id"], second["id"]])
            loaded = batches.get(batch["id"])

            self.assertEqual(loaded["project_ids"], [first["id"], second["id"]])
            self.assertEqual(loaded["status"], "pending")


if __name__ == "__main__":
    unittest.main()
