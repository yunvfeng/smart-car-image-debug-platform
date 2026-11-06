"""Rebuild editable C sources on demand and expose compiler diagnostics."""
import json
import os
from pathlib import Path
import subprocess

from .dataset import sha256
from .provenance import source_hashes

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "build/pc" / ("visionlab_replay.exe" if os.name == "nt" else "visionlab_replay")
BUILD_LOG = ROOT / "build/app-build.log"


class BuildError(RuntimeError):
    pass


def build_is_current(root: Path = ROOT, executable: Path = EXE, image_source: Path | None = None) -> bool:
    try:
        record = json.loads(executable.with_suffix(".build.json").read_text(encoding="utf-8"))
        entry = (image_source or root / "smart-car/project/code/image.c").resolve()
        return (record.get("algorithm_entry") == str(entry)
                and record["source_sha256"] == source_hashes(root, entry)
                and record["executable_sha256"] == sha256(executable))
    except (OSError, ValueError, KeyError):
        return False


def build_current(force: bool = False, image_source: Path | None = None) -> bool:
    if not force and build_is_current(ROOT, EXE, image_source):
        return False
    BUILD_LOG.parent.mkdir(exist_ok=True)
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts/build.ps1")]
    if image_source:
        command += ["-ImageSource", str(image_source.resolve())]
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=180)
    except subprocess.TimeoutExpired as error:
        BUILD_LOG.write_bytes((error.stdout or b"") + (error.stderr or b"") + b"\nBuild timed out.\n")
        raise BuildError("C 构建超时，请查看编译输出") from error
    BUILD_LOG.write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise BuildError("C 编译失败，请修正编译输出中的错误后再次回放")
    if not build_is_current(ROOT, EXE, image_source):
        raise BuildError("构建期间源码发生变化，请保存文件后再次回放")
    return True
