import json
from pathlib import Path
import subprocess
import shutil
import uuid
import unittest

import numpy as np

from visionlab.__main__ import DEFAULT_EXE, ROOT, prepare
from visionlab.dataset import load_dataset, make_demo, sha256
from visionlab.viewer import overlay


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "build" / "tests" / uuid.uuid4().hex
        self.directory.mkdir(parents=True)
        self.addCleanup(self.cleanup)
        self.dataset = self.directory / "dataset"
        make_demo(self.dataset, 8)

    def cleanup(self):
        if not self.directory.resolve().is_relative_to((ROOT / "build/tests").resolve()):
            raise ValueError("Test cleanup escaped build/tests")
        shutil.rmtree(self.directory)

    def replay(self, name="run", through=None, target=False):
        run = self.directory / name
        manifest = prepare(self.dataset, run, through, target, DEFAULT_EXE)
        subprocess.run(manifest["command"], check=True, timeout=15, capture_output=True)
        results = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
        return run, manifest, results

    def test_exact_raw_bytes_and_unmodified_dataset(self):
        before = sha256(self.dataset / "frames.npy")
        run, _, results = self.replay()
        frames, _, _ = load_dataset(self.dataset)
        self.assertEqual((run / "frames.bin").read_bytes(), frames.tobytes(order="C"))
        self.assertEqual(before, sha256(self.dataset / "frames.npy"))
        self.assertEqual(len(results), 8)

    def test_fresh_process_is_deterministic(self):
        first, _, _ = self.replay("one")
        second, _, _ = self.replay("two")
        self.assertEqual((first / "results.jsonl").read_bytes(), (second / "results.jsonl").read_bytes())

    def test_through_runs_entire_prefix(self):
        _, _, full = self.replay("full")
        _, _, prefix = self.replay("prefix", through=4)
        self.assertEqual(prefix, full[:5])
        self.assertEqual([result["index"] for result in prefix], list(range(5)))

    def test_line_arrays_and_center(self):
        _, _, results = self.replay()
        for result in results:
            for key in ("left", "right", "left_control", "right_control", "mid"):
                self.assertEqual(len(result[key]), 120)
                self.assertTrue(all(0 <= value < 188 for value in result[key]))
            for row in range(120):
                self.assertEqual(result["mid"][row],
                                 (result["left_control"][row] + result["right_control"][row]) // 2)
        self.assertLessEqual(abs(results[0]["mid"][80] - 94), 2)

    def test_black_white_and_noise(self):
        frames = np.random.default_rng(19).integers(0, 256, size=(8, 120, 188), dtype=np.uint8)
        frames[0] = 0
        frames[1] = 255
        frames[2] = 128
        np.save(self.dataset / "frames.npy", frames)
        _, _, results = self.replay(target=True)
        self.assertEqual(len(results), len(frames))

    def test_wrong_dtype_rejected(self):
        np.save(self.dataset / "frames.npy", np.zeros((8, 120, 188), dtype=np.uint16))
        with self.assertRaisesRegex(ValueError, "uint8"):
            load_dataset(self.dataset)

    def test_wrong_size_rejected(self):
        np.save(self.dataset / "frames.npy", np.zeros((8, 119, 188), dtype=np.uint8))
        with self.assertRaises(ValueError):
            load_dataset(self.dataset)

    def test_duplicate_or_out_of_order_ids_rejected(self):
        np.save(self.dataset / "frame_ids.npy", np.array([0, 1, 2, 2, 4, 5, 6, 7]))
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            load_dataset(self.dataset)

    def test_missing_processed_frame_rejected(self):
        np.save(self.dataset / "frame_ids.npy", np.array([0, 1, 2, 4, 5, 6, 7, 8]))
        with self.assertRaisesRegex(ValueError, "gap"):
            self.replay()

    def test_events_not_silently_ignored(self):
        (self.dataset / "events.bin").write_bytes(b"event")
        with self.assertRaisesRegex(ValueError, "events.bin"):
            load_dataset(self.dataset)

    def test_unknown_initial_state_rejected(self):
        (self.dataset / "metadata.json").write_text('{}')
        with self.assertRaisesRegex(ValueError, "process_start"):
            load_dataset(self.dataset)

    def test_out_of_range_index_rejected(self):
        with self.assertRaises(ValueError):
            self.replay(through=8)

    def test_overlay_preserves_input(self):
        _, _, results = self.replay()
        frames, ids, _ = load_dataset(self.dataset)
        original = frames[0].copy()
        display = overlay(frames[0], results[0], int(ids[0]))
        self.assertEqual(display.shape, (480, 752, 3))
        np.testing.assert_array_equal(original, frames[0])

    def test_existing_run_protected(self):
        run, _, _ = self.replay()
        before = sha256(run / "results.jsonl")
        with self.assertRaises(FileExistsError):
            self.replay()
        self.assertEqual(before, sha256(run / "results.jsonl"))

    def test_c_rejects_truncated_and_empty_raw(self):
        for name, content in (("truncated", bytes(22559)), ("empty", b"")):
            raw = self.directory / f"{name}.bin"
            raw.write_bytes(content)
            result = subprocess.run([str(DEFAULT_EXE), str(raw), str(raw.with_suffix(".jsonl"))],
                                    capture_output=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)

    def test_c_rejects_index_equal_to_frame_count(self):
        raw = self.directory / "single.bin"
        raw.write_bytes(bytes(22560))
        result = subprocess.run([str(DEFAULT_EXE), str(raw), str(raw.with_suffix(".jsonl")), "1"],
                                capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)

    def test_c_does_not_overwrite_existing_output(self):
        run, manifest, _ = self.replay()
        before = sha256(run / "results.jsonl")
        result = subprocess.run(manifest["command"], capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, sha256(run / "results.jsonl"))


if __name__ == "__main__":
    unittest.main()
