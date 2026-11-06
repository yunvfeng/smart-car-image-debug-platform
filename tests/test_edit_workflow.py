import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from visionlab import build
from visionlab.__main__ import ROOT
from visionlab.dataset import sha256
from visionlab.viewer import overlay_pixels

import numpy as np


class WorkflowUnitTests(unittest.TestCase):
    def test_unchanged_build_skips_compilation(self):
        with patch.object(build, "build_is_current", return_value=True), patch.object(build.subprocess, "run") as run:
            self.assertFalse(build.build_current())
            run.assert_not_called()

    def test_edited_sources_trigger_compilation(self):
        with patch.object(build, "build_is_current", side_effect=[False, True]), \
             patch.object(build.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"ok", b"")), \
             patch.object(Path, "write_bytes"):
            self.assertTrue(build.build_current())

    def test_compile_error_is_reported(self):
        with patch.object(build, "build_is_current", return_value=False), \
             patch.object(build.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"syntax error")), \
             patch.object(Path, "write_bytes"):
            with self.assertRaises(build.BuildError):
                build.build_current()

    def test_changed_during_build_is_rejected(self):
        with patch.object(build, "build_is_current", side_effect=[False, False]), \
             patch.object(build.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"ok", b"")), \
             patch.object(Path, "write_bytes"):
            with self.assertRaises(build.BuildError):
                build.build_current()

    def test_overlay_stays_at_native_resolution(self):
        frame = np.full((120, 188), 100, dtype=np.uint8)
        original = frame.copy()
        result = {name: [50] * 120 for name in ("left", "right", "left_control", "right_control", "mid")}
        result["reference_col"] = 94
        image = overlay_pixels(frame, result)
        self.assertEqual(image.shape, (120, 188, 3))
        np.testing.assert_array_equal(frame, original)
        np.testing.assert_array_equal(image[20, 94], [255, 0, 255])


@unittest.skipUnless(os.name == "nt", "Uses the installed Windows GCC/CMake toolchain")
class EditableCIntegrationTest(unittest.TestCase):
    def test_save_build_replay_and_compile_failure(self):
        """Compile a private checkout, change a real threshold, and verify new output."""
        directory = ROOT / "build" / "edit-tests" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        original_source_hash = sha256(ROOT / "smart-car/project/code/image.c")
        external = directory.parent / (directory.name + "-external") / "外部算法 文件夹"
        try:
            shutil.copytree(ROOT / "smart-car/project/code", directory / "smart-car/project/code")
            shutil.copytree(ROOT / "smart-car/.git", directory / "smart-car/.git",
                            ignore=shutil.ignore_patterns("hooks", "logs"), copy_function=shutil.copyfile)
            shutil.copytree(ROOT / "visionlab", directory / "visionlab", ignore=shutil.ignore_patterns("__pycache__"))
            (directory / "scripts").mkdir()
            for name in ("build.ps1", "stamp_build.py"):
                shutil.copy2(ROOT / "scripts" / name, directory / "scripts" / name)
            shutil.copy2(ROOT / "CMakeLists.txt", directory / "CMakeLists.txt")
            script = directory / "verify.py"
            script.write_text('''
import json
import hashlib
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
import subprocess
from visionlab.build import build_current, build_is_current, BuildError, BUILD_LOG
from visionlab.dataset import make_demo
from visionlab.__main__ import prepare, DEFAULT_EXE
from visionlab.issue_bundle import export_issue_bundle

make_demo(Path("dataset"), 4)
def replay(name):
    manifest = prepare(Path("dataset"), Path(name), None, False, DEFAULT_EXE)
    subprocess.run(manifest["command"], check=True)
    return [json.loads(line) for line in (Path(name)/"results.jsonl").read_text().splitlines()]

assert build_current()
before = replay("before")
source = Path("smart-car/project/code/image.c")
original = source.read_bytes()
changed = original.replace(b"#define WHITE_MAX_OFFSET               24u", b"#define WHITE_MAX_OFFSET               25u")
assert changed != original
source.write_bytes(changed)
assert not build_is_current()
assert build_current()
after = replay("after")
assert after[0]["white_max"] == before[0]["white_max"] + 1
assert not build_current()
assert build_current(image_source=Path("smart-car/project/code/ring.c"))
assert replay("ring-profile") == after
# Select a renamed C entry outside the application tree, including a Unicode folder.
external = Path(sys.argv[1])
shutil.copytree(Path("smart-car/project/code"), external)
entry = external / "new_image.c"
entry.write_bytes(original.replace(b"#define WHITE_MAX_OFFSET               24u", b"#define WHITE_MAX_OFFSET               26u"))
assert build_current(image_source=entry)
external_results = replay("external-run")
assert external_results[0]["white_max"] == before[0]["white_max"] + 2
assert build_is_current(image_source=entry)
# Later edits must not change the algorithm attached to an older experiment.
entry.write_bytes(entry.read_bytes().replace(b"26u", b"27u", 1))
run = Path("external-run")
record = json.loads((run/"run.json").read_text(encoding="utf-8"))
record["status"] = "complete"
(run/"run.json").write_text(json.dumps(record), encoding="utf-8")
export_issue_bundle(run, Path("external-case.zip"), [1,3], "external algorithm", {})
with zipfile.ZipFile("external-case.zip") as archive:
    assert b"WHITE_MAX_OFFSET               26u" in archive.read("algorithm/external/new_image.c")
    archive.extractall("external-repro")
alias = Path(tempfile.gettempdir()) / ("VisionLab-" + hashlib.sha256(str(Path.cwd()).encode()).hexdigest()[:12].upper())
check = subprocess.run([sys.executable, str(alias/"external-repro/reproduce.py")], cwd=alias/"external-repro", capture_output=True)
assert check.returncode == 0, (check.stdout+check.stderr).decode(errors="replace")
source.write_bytes(changed + b"\\n#error VisionLab_compile_failure_test\\n")
try:
    build_current()
except BuildError:
    assert "VisionLab_compile_failure_test" in BUILD_LOG.read_text(errors="replace")
else:
    raise AssertionError("Compilation failure was ignored")
assert not build_is_current()
print("PASS: edited original image.c, auto-rebuilt, changed C output, compile errors blocked")
''', encoding="utf-8")
            result = subprocess.run([sys.executable, str(script), str(external)], cwd=directory, capture_output=True, timeout=120)
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode(errors="replace"))
            self.assertIn(b"changed C output", result.stdout)
            self.assertEqual(original_source_hash, sha256(ROOT / "smart-car/project/code/image.c"))
        finally:
            # Remove only the junction created for this isolated checkout.
            alias_name = "VisionLab-" + hashlib.sha256(str(directory).encode()).hexdigest()[:12].upper()
            alias = Path(tempfile.gettempdir()) / alias_name
            if alias.is_junction() and alias.resolve() == directory.resolve():
                os.rmdir(alias)
            for source_directory in (directory / "smart-car/project/code", external):
                source_alias_name = "VisionLab-source-" + hashlib.sha256(str(source_directory).encode()).hexdigest()[:12].upper()
                source_alias = Path(tempfile.gettempdir()) / source_alias_name
                if source_alias.is_junction() and source_alias.resolve() == source_directory.resolve():
                    os.rmdir(source_alias)
            if not directory.resolve().is_relative_to((ROOT / "build/edit-tests").resolve()):
                raise ValueError("Cleanup escaped build/edit-tests")
            shutil.rmtree(directory)
            external_parent = external.parent.resolve()
            if external_parent.is_relative_to((ROOT / "build/edit-tests").resolve()) and external_parent.exists():
                shutil.rmtree(external_parent)


if __name__ == "__main__":
    unittest.main()
