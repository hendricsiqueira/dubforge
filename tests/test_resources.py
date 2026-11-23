import tempfile
import unittest
from pathlib import Path

from dubforge.resources import AdaptiveScheduler, ResourceMonitor, ResourceSnapshot


def snapshot(free_vram=None, total_vram=None):
    return ResourceSnapshot(
        captured_at=0,
        disk_total_gb=100,
        disk_free_gb=50,
        ram_total_gb=64,
        ram_available_gb=40,
        gpu_name="Test GPU" if total_vram else None,
        gpu_vram_total_gb=total_vram,
        gpu_vram_used_gb=(total_vram - free_vram) if total_vram and free_vram else None,
        gpu_vram_free_gb=free_vram,
    )


class ResourceTests(unittest.TestCase):
    def test_scheduler_is_conservative_on_24gb_and_scales_on_48gb(self):
        auto = AdaptiveScheduler("Automático")
        self.assertEqual(auto.tts_workers(snapshot(20, 24)), 1)
        self.assertEqual(auto.tts_workers(snapshot(44, 48)), 3)
        self.assertEqual(auto.translation_batch_size(snapshot(28, 32)), 16)

    def test_economic_mode_always_uses_one_worker(self):
        economic = AdaptiveScheduler("Econômico")
        self.assertEqual(economic.tts_workers(snapshot(44, 48)), 1)
        self.assertEqual(economic.translation_batch_size(snapshot(44, 48)), 4)

    def test_resource_monitor_reads_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor = ResourceMonitor(Path(tmp))
            current = monitor.sample()
            self.assertGreater(current.disk_total_gb, 0)
            self.assertGreaterEqual(current.disk_free_gb, 0)


if __name__ == "__main__":
    unittest.main()
