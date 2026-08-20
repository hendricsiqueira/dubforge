import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from dubforge.pipeline import DubPipeline
from dubforge.zast_bridge import ZastBridge


class PipelineTests(unittest.TestCase):
    def test_timestamp_rounding(self):
        self.assertEqual(DubPipeline._timestamp(0), "00:00:00,000")
        self.assertEqual(DubPipeline._timestamp(65.4321), "00:01:05,432")
        self.assertEqual(DubPipeline._timestamp(3661.9996), "01:01:02,000")

    def test_venv_scripts_are_prepended_to_path_once(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"PATH": "existing"}):
            scripts = Path(tmp) / "Scripts"
            scripts.mkdir()
            ZastBridge._prepend_to_path(scripts)
            ZastBridge._prepend_to_path(scripts)
            entries = os.environ["PATH"].split(os.pathsep)
            self.assertEqual(entries[0], str(scripts.resolve()))
            self.assertEqual(entries.count(str(scripts.resolve())), 1)


if __name__ == "__main__":
    unittest.main()
