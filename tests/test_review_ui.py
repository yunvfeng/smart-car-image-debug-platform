import json
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from visionlab.app import VisionLabApp

sys.path.insert(0, str(Path(__file__).parent))
import test_issue_bundle as bundle_tests


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class ReviewUITests(unittest.TestCase):
    def setUp(self):
        self.fixture = bundle_tests.IssueBundleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = VisionLabApp(self.root)
        self.app.load_run(self.fixture.run)

    def test_source_browser_binds_selected_file(self):
        path = str(self.fixture.directory / "elsewhere/new_image.c")
        with patch("visionlab.app.filedialog.askopenfilename", return_value=path):
            self.app.browse_source()
        self.assertEqual(self.app.source_file.get(), path)

    def test_playback_rate_and_deadlines(self):
        self.app.rate_text.set("120")
        self.assertTrue(self.app.set_playback_rate())
        self.app.playing = True
        self.app.next_frame_at = 100
        with patch("visionlab.app.time.monotonic", return_value=100):
            self.app.play_tick()
        self.assertEqual(self.app.index, 1)
        with patch("visionlab.app.time.monotonic", return_value=100.002):
            self.app.play_tick()
        self.assertEqual(self.app.index, 1)
        self.app.rate_text.set("nan")
        self.assertFalse(self.app.set_playback_rate())
        self.assertEqual(self.app.playback_rate, 120)

    def test_markers_persist_and_jump(self):
        self.app.show_frame(3)
        self.app.mark_frame()
        self.assertIn("3", self.app.review["marks"])
        self.assertGreaterEqual(len(self.app.markers.find_all()), 3)
        self.app.load_run(self.fixture.run)
        self.assertIn("3", self.app.review["marks"])
        width = max(32, self.app.markers.winfo_width())
        self.app.marker_click(SimpleNamespace(x=8 + 3 * (width - 16) / 7))
        self.assertEqual(self.app.index, 3)

    def test_dialog_exports_selected_frames_and_description(self):
        self.app.show_frame(3)
        self.app.mark_frame()
        self.app.show_frame(5)
        self.app.mark_frame()
        self.app.open_issue_dialog()
        window = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
        notes = next(child for child in descendants(window) if isinstance(child, ScrolledText))
        notes.insert("1.0", "第三帧边线出现跳变，请定位并修复。")
        button = next(child for child in descendants(window) if isinstance(child, ttk.Button)
                      and child.cget("text") == "导出单文件调试包")
        target = self.fixture.directory / "ui-case.zip"
        with patch("visionlab.app.filedialog.asksaveasfilename", return_value=str(target)):
            button.invoke()
        deadline = time.monotonic() + 10
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy)
        with zipfile.ZipFile(target) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["selected_indices"], [3, 5])
            self.assertIn("请定位并修复", archive.read("description.txt").decode("utf-8"))

    def test_unmark_clears_selected_frame_and_both_timeline_symbols(self):
        self.app.show_frame(3)
        self.app.mark_frame()
        self.assertIn(3, self.app.review["selected"])
        self.app.mark_frame()
        self.assertNotIn("3", self.app.review["marks"])
        self.assertNotIn(3, self.app.review["selected"])
        self.assertEqual(len(self.app.markers.find_all()), 0)
        self.app.load_run(self.fixture.run)
        self.assertNotIn("3", self.app.review["marks"])
        self.assertNotIn(3, self.app.review["selected"])


if __name__ == "__main__":
    unittest.main()
