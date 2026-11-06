"""Exercise a real conditional C breakpoint, stepping, arrays and watchpoint."""
from pathlib import Path
import subprocess
import sys
import shutil
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from visionlab.__main__ import DEFAULT_EXE, prepare
from visionlab.dataset import make_demo

directory = ROOT / "build" / ("gdb-test-" + uuid.uuid4().hex)
directory.mkdir(parents=True)
try:
    make_demo(directory / "dataset", 528)
    manifest = prepare(directory / "dataset", directory / "run", 527, False, DEFAULT_EXE)
    script = directory / "check.gdb"
    script.write_text("\n".join([
        "set pagination off", "set confirm off", "set print elements 10",
        "break Search_line if visionlab_frame_index == 527", "run",
        'printf "VISIONLAB_INDEX=%lld\\n", visionlab_frame_index',
        "bt", "next", 'printf "VISIONLAB_ROW_MAX=%u\\n", row_max',
        "print /u left_edge_line[80]@5", "print /u mt9v03x_image[0][0]@8",
        "watch left_edge_line[80]", "continue", "bt", "quit", "",
    ]), encoding="ascii")
    # A command file also works with this GDB build's Windows -ex quoting issue.
    result = subprocess.run(["gdb", "-q", "-batch", "-x", str(script), "--args", *manifest["command"]],
                            cwd=ROOT, capture_output=True, timeout=60)
    log = result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace")
    (ROOT / "build/gdb-smoke.log").write_text(log, encoding="utf-8")
    required = ("VISIONLAB_INDEX=527", "VISIONLAB_ROW_MAX=119", "Image_OldStyle_Process",
                "Hardware watchpoint", "Old value", "New value")
    if result.returncode or not all(marker in log for marker in required) or "No such file" in log:
        sys.stdout.buffer.write(result.stdout + result.stderr)
        raise SystemExit("GDB smoke test failed")
    print("PASS: index 527, original Search_line source, next, arrays, stack and hardware watchpoint")
finally:
    if not directory.resolve().is_relative_to((ROOT / "build").resolve()):
        raise ValueError("GDB test cleanup escaped build")
    shutil.rmtree(directory)
