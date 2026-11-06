"""Windowed desktop entry point with a readable error if startup fails."""
import ctypes
import os
from pathlib import Path
import sys
import traceback

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
runtime = root / "runtime"
if runtime.is_dir():
    tools = [runtime, root / "tools/mingw64/bin", root / "tools/cmake/bin"]
    os.environ["PATH"] = os.pathsep.join([*(str(path) for path in tools), os.environ.get("PATH", "")])
try:
    from visionlab.app import main
    main()
except Exception:
    message = traceback.format_exc()
    (root / "build").mkdir(exist_ok=True)
    (root / "build/startup-error.log").write_text(message, encoding="utf-8")
    ctypes.windll.user32.MessageBoxW(None, message, "VisionLab startup error", 0x10)
