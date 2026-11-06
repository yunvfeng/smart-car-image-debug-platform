"""Build a self-contained Windows ZIP from the local Python and C toolchains."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from visionlab.provenance import source_files


def copy_directory(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise SystemExit("Build this release using 64-bit Python on Windows")
    version_match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-[A-Za-z0-9.-]+)?", args.version)
    if not version_match or any(int(part) > 65535 for part in version_match.groups()):
        raise SystemExit("Invalid release version")
    python_root = Path(sys.base_prefix)
    gcc = Path(shutil.which("gcc") or "missing-gcc").resolve()
    cmake = Path(shutil.which("cmake") or "missing-cmake").resolve()
    if not gcc.is_file() or not cmake.is_file():
        raise SystemExit("GCC and CMake must be available on PATH")
    mingw_root, cmake_root = gcc.parent.parent, cmake.parent.parent
    destination = args.output.resolve() / f"VisionLab-{args.version}-windows-x64"
    archive_path = destination.with_name(destination.name + ".zip")
    if destination.exists() or archive_path.exists():
        raise SystemExit("Choose a fresh output directory or version")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    firmware_revision = subprocess.check_output(["git", "-C", str(ROOT / "smart-car"), "rev-parse", "HEAD"], text=True).strip()
    destination.mkdir(parents=True)
    print("Copying application sources", flush=True)
    paths = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    for name in paths:
        source = ROOT / name
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for name, source in source_files(ROOT).items():
        if name.startswith("smart-car/"):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (destination / "smart-car/REVISION").write_text(firmware_revision + "\n", encoding="utf-8")
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"):
        license_path = ROOT / "smart-car" / name
        if license_path.is_file():
            shutil.copy2(license_path, destination / "smart-car" / name)

    print("Copying private Python runtime and GUI libraries", flush=True)
    runtime = destination / "runtime"
    runtime.mkdir()
    for pattern in ("*.exe", "*.dll", "LICENSE*.txt"):
        for path in python_root.glob(pattern):
            shutil.copy2(path, runtime / path.name)
    copy_directory(python_root / "DLLs", runtime / "DLLs")
    copy_directory(python_root / "tcl", runtime / "tcl")
    shutil.copytree(python_root / "Lib", runtime / "Lib", ignore=shutil.ignore_patterns(
        "site-packages", "__pycache__", "*.pyc", "*.pyo", "test", "tests", "idlelib", "ensurepip", "turtledemo"))
    packages = python_root / "Lib/site-packages"
    bundled_packages = runtime / "Lib/site-packages"
    bundled_packages.mkdir()
    for name in ("numpy", "numpy.libs", "cv2", "PIL", "serial"):
        copy_directory(packages / name, bundled_packages / name)
    distributions = {}
    for name in ("numpy", "opencv-contrib-python", "Pillow", "pyserial"):
        distribution = importlib.metadata.distribution(name)
        distributions[name] = distribution.version
        for entry in distribution.files or []:
            if entry.parts[0].endswith(".dist-info"):
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    target = bundled_packages / entry
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    dll_name = f"python{sys.version_info.major}{sys.version_info.minor}"
    (runtime / (dll_name + "._pth")).write_text("Lib\nDLLs\nLib/site-packages\n..\nimport site\n", encoding="utf-8")

    print("Copying portable GCC, Make, GDB and CMake", flush=True)
    copy_directory(mingw_root, destination / "tools/mingw64")
    copy_directory(cmake_root, destination / "tools/cmake")
    print("Building the Windows launcher", flush=True)
    with tempfile.TemporaryDirectory(prefix="VisionLab-launcher-") as temporary:
        native_build = Path(temporary)
        shutil.copy2(ROOT / "scripts/windows_launcher.c", native_build / "launcher.c")
        shutil.copy2(ROOT / "visionlab/assets/visionlab.ico", native_build / "visionlab.ico")
        resource_text = (ROOT / "scripts/windows_launcher.rc").read_text(encoding="utf-8")
        resource_text = resource_text.replace("@VERSION_COMMAS@", ",".join([*version_match.groups(), "0"]))
        resource_text = resource_text.replace("@VERSION@", args.version)
        (native_build / "launcher.rc").write_text(resource_text, encoding="utf-8")
        subprocess.run([str(gcc.parent / "windres.exe"), "launcher.rc", "launcher.res.o"], cwd=native_build, check=True)
        subprocess.run([str(gcc), "-Os", "-municode", "-mwindows", "-static", "-Wall", "-Wextra",
                        "launcher.c", "launcher.res.o", "-o", "VisionLab.exe"], cwd=native_build, check=True)
        shutil.copy2(native_build / "VisionLab.exe", destination / "VisionLab.exe")
    (destination / "python.cmd").write_text(
        '@echo off\r\nsetlocal\r\nset "PATH=%~dp0runtime;%~dp0tools\\mingw64\\bin;%~dp0tools\\cmake\\bin;%PATH%"\r\n'
        '"%~dp0runtime\\python.exe" %*\r\nexit /b %ERRORLEVEL%\r\n', encoding="utf-8")
    (destination / "使用说明.txt").write_text(
        f"VisionLab {args.version} · 智能车图像调试平台\n\n"
        "1. 将整个文件夹解压到可写目录，双击 VisionLab.exe 启动。\n"
        "2. 在图像实验室点击生成测试数据，再点击构建并回放，首次使用会自动编译 C 源码。\n"
        "3. 选择 C 文件可加载其他目录的兼容算法，修改后再次构建并回放。\n"
        "4. 在实车工作台选择 TCP 服务端或串口，连接后即可调参、录制和导出。\n"
        "5. 录制保存在 datasets，实验保存在 runs，日志保存在 build。\n\n"
        "运行环境与编译工具已随包提供。VS Code 编辑与断点调试可配合本机安装的 VS Code 使用。\n"
        "可将 VisionLab.exe 的快捷方式发送到桌面。需要运行导出调试包的 reproduce.py 时，\n"
        "在调试包目录调用本发行包中的 python.cmd reproduce.py。\n\n"
        "手动验收\n"
        "启动与窗口缩放、首次编译回放、外部源码重编译、播放调速、7-15 区间选帧、\n"
        "取消异常标记、AI ZIP 导出、TCP/串口连接与录制。\n\n"
        "项目地址 https://github.com/yunvfeng/smart-car-image-debug-platform\n", encoding="utf-8-sig")
    (destination / "THIRD-PARTY-NOTICES.txt").write_text(
        "Python: runtime/LICENSE.txt, https://www.python.org/\n"
        "NumPy, OpenCV, Pillow, pyserial: runtime/Lib/site-packages/*.dist-info\n"
        "MinGW-Builds toolchain: tools/mingw64/licenses and build-info.txt\n"
        "Toolchain source recipes and patches: https://github.com/niXman/mingw-builds\n"
        "Binary distribution: https://github.com/niXman/mingw-builds-binaries/releases\n"
        "Component source archive URLs are recorded in tools/mingw64/build-info.txt\n"
        "CMake: tools/cmake/doc and tools/cmake/share, https://cmake.org/\n"
        "Default vision algorithm: https://github.com/yunvfeng/smart-car\n", encoding="utf-8")
    metadata = {"version": args.version, "platform": "windows-x64", "source_commit": revision,
                "firmware_commit": firmware_revision, "python": sys.version,
                "packages": distributions, "built_at": datetime.now(timezone.utc).isoformat(),
                "validation": "Build and archive integrity checks; functional acceptance is manual"}
    (destination / "release.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Writing file hashes and ZIP archive", flush=True)
    members = sorted(path for path in destination.rglob("*") if path.is_file())
    hashes = [f"{file_hash(path)}  {path.relative_to(destination).as_posix()}" for path in members]
    (destination / "files.sha256").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(destination.parent).as_posix())
    print("Checking ZIP CRC integrity", flush=True)
    with zipfile.ZipFile(archive_path) as archive:
        damaged = archive.testzip()
        if damaged:
            raise RuntimeError("ZIP integrity failed: " + damaged)
    checksum = archive_path.with_name(archive_path.name + ".sha256")
    checksum.write_text(f"{file_hash(archive_path)}  {archive_path.name}\n", encoding="utf-8")
    print(json.dumps({"archive": str(archive_path), "bytes": archive_path.stat().st_size,
                      "checksum": str(checksum), "files": len(members) + 1}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
