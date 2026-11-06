"""Windowed desktop entry point with a readable error if startup fails."""
import ctypes
import os
from pathlib import Path
import sys
import traceback

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
try:
    from visionlab.app import main
    main()
except Exception:
    message = traceback.format_exc()
    (root / "build").mkdir(exist_ok=True)
    (root / "build/startup-error.log").write_text(message, encoding="utf-8")
    ctypes.windll.user32.MessageBoxW(None, message, "VisionLab startup error", 0x10)
