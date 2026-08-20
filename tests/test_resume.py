import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dubforge.pipeline import DubPipeline
from dubforge.store import ProjectStore


class FakeBridge:
    prepared = 0
    translated = 0
    synthesized = 0

    def __init__(self, zast_path, project_dir):
        self.project_dir = Path(project_dir)

    def prepare_source(self, source_path, source_language, whisper_model, preserve_background, callback):
        type(self).prepared += 1
        reference = self.project_dir / "cache" / "voice_reference.wav"
        reference.write_bytes(b"voice")
        return {
            "duration": 2.0,
            "detected_language": "pt",
            "vocals": str(reference),
            "background": None,
            "voice_reference": str(reference),
            "segments": [{"start": 0.0, "end": 1.0, "text": "Olá"}],
        }

    def translate_language(self, segments, source_language_code, target_language, llm_backend, callback):
        type(self).translated += 1
        return [{**segments[0], "translated_text": "Hello", "normal_text": "Hello"}]

    def unload_llm(self, llm_backend):
        pass

    @staticmethod
    def _synthesis_signature(translated, voice_reference, never_cut, bitrate, preserve_background):
        return "stable-signature"

    def synthesize_language(self, translated, target_language, duration, voice_reference,
                            background_path, never_cut, bitrate, source_stem, callback):
        type(self).synthesized += 1
        output = self.project_dir / "outputs" / "en" / f"{source_stem}_en.mp3"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp3")
        return {"mp3": str(output), "stats": {"total": 1}, "signature": "stable-signature"}

    def unload_tts(self):
        pass

    @staticmethod
    def release_vram():
        pass


class ResumeTests(unittest.TestCase):
    def setUp(self):
        FakeBridge.prepared = 0
        FakeBridge.translated = 0
        FakeBridge.synthesized = 0

    def test_second_run_reuses_every_completed_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            store = ProjectStore(root / "projects")
            settings = {
                "source_language": "Portuguese",
                "whisper_model": "large-v3",
                "target_languages": ["English"],
                "voice_mode": "Clonar voz original",
                "voice_file": None,
                "never_cut": False,
                "bitrate": "320k",
                "generate_srt": True,
                "preserve_background": False,
                "llm_backend": "Qwen2.5-7B-Instruct",
                "tts_backend": "VoxCPM 2",
            }
            project = store.create("Teste", source, settings)
            pipeline = DubPipeline(store, root / "unused-zast")

            with patch("dubforge.pipeline.ZastBridge", FakeBridge):
                first = pipeline.run(project["id"], lambda *args: None)
                second = pipeline.run(project["id"], lambda *args: None)

            self.assertEqual(FakeBridge.prepared, 1)
            self.assertEqual(FakeBridge.translated, 1)
            self.assertEqual(FakeBridge.synthesized, 1)
            self.assertEqual(first, second)
            self.assertTrue(any(path.endswith(".mp3") for path in second))
            self.assertTrue(any(path.endswith(".srt") for path in second))


if __name__ == "__main__":
    unittest.main()
