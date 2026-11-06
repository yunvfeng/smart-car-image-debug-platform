import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
import zipfile

import numpy as np
from PIL import Image

from visionlab.__main__ import ROOT, DEFAULT_EXE, prepare, write_manifest
from visionlab.dataset import make_demo, sha256
from visionlab.issue_bundle import export_issue_bundle
from visionlab.review import load_review, parse_selection, save_review


class IssueBundleTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "build/issue-tests" / uuid.uuid4().hex
        self.directory.mkdir(parents=True)
        self.addCleanup(self.cleanup)
        self.dataset = self.directory / "dataset"
        self.run = self.directory / "run"
        make_demo(self.dataset, 8)
        self.manifest = prepare(self.dataset, self.run, None, False, DEFAULT_EXE)
        subprocess.run(self.manifest["command"], check=True, capture_output=True, timeout=15)
        self.manifest.update(status="complete", results_sha256=sha256(self.run / "results.jsonl"))
        write_manifest(self.run, self.manifest)
        self.review = {"schema_version": 1, "marks": {"3": {"frame_id": 3, "note": "右边线错误"}},
                       "selected": [3, 5], "notes": "第3帧右侧误识别，请分析原因。"}

    def cleanup(self):
        if not self.directory.resolve().is_relative_to((ROOT / "build/issue-tests").resolve()):
            raise ValueError("Cleanup escaped issue-tests")
        shutil.rmtree(self.directory)

    def export(self):
        target = self.directory / "case.zip"
        export_issue_bundle(self.run, target, [5, 3, 3], self.review["notes"], self.review)
        return target

    def test_selected_exact_inputs_outputs_and_full_history(self):
        target = self.export()
        frames = np.load(self.dataset / "frames.npy")
        results = [json.loads(line) for line in (self.run / "results.jsonl").read_text().splitlines()]
        with zipfile.ZipFile(target) as archive:
            metadata = json.loads(archive.read("manifest.json"))
            self.assertEqual(metadata["selected_indices"], [3, 5])
            self.assertEqual(metadata["replay_prefix"], [0, 5])
            selected = np.load(io.BytesIO(archive.read("selected/frames.npy")), allow_pickle=False)
            np.testing.assert_array_equal(selected, frames[[3, 5]])
            self.assertEqual(archive.read("replay/frames.bin"), frames[:6].tobytes())
            raw_png = np.array(Image.open(io.BytesIO(archive.read("selected/raw/frame_000003.png"))))
            np.testing.assert_array_equal(raw_png, frames[3])
            actual = [json.loads(line) for line in archive.read("selected/c_results.jsonl").splitlines()]
            self.assertEqual(actual, [results[3], results[5]])
            self.assertIn("右侧误识别", archive.read("description.txt").decode("utf-8"))
            self.assertIn("algorithm/visionlab/c/replay.c", archive.namelist())
            self.assertNotIn("algorithm/smart-car/project/code/wifi_assistant.c", archive.namelist())
            for name, expected in metadata["files_sha256"].items():
                import hashlib
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)

    def test_snapshot_tampering_is_rejected(self):
        (self.run / "algorithm/smart-car/project/code/image.c").write_text("changed")
        with self.assertRaisesRegex(ValueError, "快照"):
            self.export()

    def test_raw_tampering_is_rejected(self):
        with (self.run / "frames.bin").open("r+b") as stream:
            stream.write(b"bad")
        with self.assertRaisesRegex(ValueError, "输入"):
            self.export()

    def test_output_tampering_is_rejected(self):
        with (self.run / "results.jsonl").open("a") as stream:
            stream.write("{}\n")
        with self.assertRaisesRegex(ValueError, "输出"):
            self.export()

    def test_selection_bounds_and_existing_export(self):
        with self.assertRaises(ValueError):
            export_issue_bundle(self.run, self.directory / "bad.zip", [8], "", self.review)
        self.export()
        before = sha256(self.directory / "case.zip")
        with self.assertRaises(FileExistsError):
            self.export()
        self.assertEqual(before, sha256(self.directory / "case.zip"))

    def test_review_and_legacy_markers(self):
        (self.run / "annotations.jsonl").write_text('{"index":3,"frame_id":3}\n', encoding="utf-8")
        legacy = load_review(self.run, 8)
        self.assertEqual(legacy["selected"], [3])
        save_review(self.run, self.review)
        self.assertEqual(load_review(self.run, 8), self.review)

    def test_package_can_rebuild_and_reproduce(self):
        # An ASCII junction avoids the installed MinGW Make's Unicode path limitation.
        target = self.export()
        destination = self.directory / "extracted"
        with zipfile.ZipFile(target) as archive:
            archive.extractall(destination)
        import os
        import tempfile
        alias = Path(tempfile.gettempdir()) / ("VisionLab-case-" + uuid.uuid4().hex)
        try:
            command = ["powershell", "-NoProfile", "-Command",
                       "New-Item -ItemType Junction -Path $env:VISIONLAB_TEST_ALIAS -Target $env:VISIONLAB_TEST_TARGET | Out-Null"]
            environment = dict(os.environ, VISIONLAB_TEST_ALIAS=str(alias), VISIONLAB_TEST_TARGET=str(destination))
            subprocess.run(command, env=environment, check=True, capture_output=True, timeout=15)
            result = subprocess.run([sys.executable, "reproduce.py"], cwd=alias, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode(errors="replace"))
            self.assertIn(b"all prefix outputs match", result.stdout)
        finally:
            if alias.is_junction() and alias.resolve() == destination.resolve():
                os.rmdir(alias)


class SelectionTests(unittest.TestCase):
    def test_ranges_and_duplicates(self):
        self.assertEqual(parse_selection("1,3-5，3 7", 8), [1, 3, 4, 5, 7])

    def test_invalid_selection(self):
        for text in ("-1", "1-9", "4-2", "1.5", "x", "1--2"):
            with self.assertRaises(ValueError):
                parse_selection(text, 8)


if __name__ == "__main__":
    unittest.main()
