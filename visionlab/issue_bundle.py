"""A single portable AI bug report with exact inputs, outputs and source bytes."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import uuid
import zipfile

import numpy as np
from PIL import Image

from .dataset import sha256
from .viewer import overlay
from .provenance import source_files


def npy_bytes(array: np.ndarray) -> bytes:
    stream = io.BytesIO()
    np.save(stream, array, allow_pickle=False)
    return stream.getvalue()


def export_issue_bundle(run: Path, target: Path, selected: list[int], notes: str, review: dict) -> None:
    """Keep the full prefix needed to reproduce state machines, plus selected AI evidence."""
    run, target = run.resolve(), target.resolve()
    if target.exists():
        raise FileExistsError("请选择新的调试包文件名")
    if target.is_relative_to(run):
        raise ValueError("请把调试包保存到实验目录之外")
    manifest = json.loads((run / "run.json").read_text(encoding="utf-8"))
    count = manifest["through_index"] + 1
    selected = sorted(set(selected))
    if not selected or any(not isinstance(index, int) or not 0 <= index < count for index in selected):
        raise ValueError("请先选择有效的帧索引")
    if manifest.get("status") != "complete":
        raise ValueError("调试包需要已完成的回放实验")
    raw = run / "frames.bin"
    if raw.stat().st_size != count * 22560 or sha256(raw) != manifest["raw_sha256"]:
        raise ValueError("实验的原始输入已损坏或发生变化")
    ids = np.array(manifest["frame_ids"], dtype=np.uint64)
    if len(ids) != count:
        raise ValueError("实验帧号记录不完整")
    results = [json.loads(line) for line in (run / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    if manifest.get("results_sha256") and sha256(run / "results.jsonl") != manifest["results_sha256"]:
        raise ValueError("实验的 C 输出已损坏或发生变化")
    if len(results) != count or any(result["index"] != i for i, result in enumerate(results)):
        raise ValueError("C 输出缺少连续帧")

    sources = {}
    recorded_hashes = manifest["source_sha256"]
    if not manifest.get("source_files"):
        root = Path(__file__).resolve().parents[1]
        required = source_files(root)
        recorded_hashes = {name: recorded_hashes[name] for name in required}
    for name, expected in recorded_hashes.items():
        member = PurePosixPath(name)
        if member.is_absolute() or ".." in member.parts or ":" in name or "\\" in name:
            raise ValueError("Invalid source path in experiment manifest")
        path = run / "algorithm" / name
        if not path.exists():
            # Older experiments can be exported only while their original sources still match.
            path = Path(manifest.get("source_files", {}).get(name, Path(__file__).resolve().parents[1] / name))
        if not path.is_file() or sha256(path) != expected:
            raise ValueError("本实验没有可用的对应源码快照，请重新构建并回放一次后导出")
        sources[name] = path.read_bytes()
        if hashlib.sha256(sources[name]).hexdigest() != expected:
            raise ValueError("源码在导出期间发生变化，请重试")
    entry = manifest.get("algorithm_entry")
    entry_key = next((name for name, original in manifest.get("source_files", {}).items()
                      if original == entry), "smart-car/project/code/image.c")
    if entry_key not in sources:
        raise ValueError("实验缺少算法入口源码")

    last = selected[-1]
    frames = np.memmap(raw, mode="r", dtype=np.uint8, shape=(count, 120, 188))
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    bundle = {"schema_version": 1, "selected_indices": selected,
              "selected_frame_ids": [int(ids[i]) for i in selected],
              "replay_prefix": [0, last], "history_frames": last + 1,
              "algorithm_entry": entry_key, "target_detect": manifest["target_detect"],
              "exploratory": manifest.get("exploratory", False),
              "source_sha256": recorded_hashes, "files_sha256": {},
              "limitations": manifest.get("limitations", [])}
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            def write(name: str, data: bytes) -> None:
                archive.writestr(name, data)
                bundle["files_sha256"][name] = hashlib.sha256(data).hexdigest()

            write("description.txt", notes.encode("utf-8"))
            write("review.json", json.dumps(review, ensure_ascii=False, indent=2).encode("utf-8"))
            write("experiment.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            write("selected/frames.npy", npy_bytes(np.asarray(frames[selected])))
            write("selected/indices.npy", npy_bytes(np.array(selected, dtype=np.int64)))
            write("selected/frame_ids.npy", npy_bytes(ids[selected]))
            write("selected/c_results.jsonl", ("\n".join(json.dumps(results[i]) for i in selected) + "\n").encode())
            for index in selected:
                stream = io.BytesIO()
                Image.fromarray(frames[index]).save(stream, format="PNG")
                write(f"selected/raw/frame_{index:06d}.png", stream.getvalue())
                stream = io.BytesIO()
                display = overlay(frames[index], results[index], int(ids[index]))
                Image.fromarray(display[:, :, ::-1]).save(stream, format="PNG")
                write(f"selected/overlays/frame_{index:06d}.png", stream.getvalue())
            # Stream the complete prefix instead of concatenating a long clip into RAM.
            digest = hashlib.sha256()
            with archive.open("replay/frames.bin", "w") as output, raw.open("rb") as source:
                remaining = (last + 1) * 22560
                while remaining:
                    data = source.read(min(1024 * 1024, remaining))
                    if not data:
                        raise ValueError("原始输入在导出期间被截断")
                    output.write(data)
                    digest.update(data)
                    remaining -= len(data)
            bundle["files_sha256"]["replay/frames.bin"] = digest.hexdigest()
            write("replay/frame_ids.npy", npy_bytes(ids[:last + 1]))
            write("replay/expected.jsonl", ("\n".join(json.dumps(r) for r in results[:last + 1]) + "\n").encode())
            write("replay/metadata.json", json.dumps(manifest["metadata"], ensure_ascii=False, indent=2).encode("utf-8"))
            for name, data in sources.items():
                write("algorithm/" + name, data)
            write("reproduce.py", REPRODUCE.encode("utf-8"))
            summary = f'''# VisionLab AI 调试包

## 问题说明

{notes}

## 重点帧

索引 {selected}，对应设备/接收帧号 {[int(ids[i]) for i in selected]}。
每帧备注与异常标记见 review.json。

## 分析入口

1. 阅读 description.txt 与 review.json，核对 experiment.json 的运行模式和限制。
2. 对照 selected/raw 的无损原始灰度 PNG 与 selected/overlays 的结果预览。
3. 检查 selected/c_results.jsonl 中的原始边线、补线后控制线、中线、阈值和状态。
4. algorithm 包含实验对应的 C 源码及头文件快照，入口为 {entry_key}。
5. 回放需要状态历史，本包包含索引 0 到 {last} 的全部输入和 C 输出。先顺序重现，再定位重点帧。

## 本地重现

把本包解压到 ASCII 路径，在具有 GCC、CMake 和 MinGW Make 的 Windows 上执行 `python reproduce.py`。
脚本构建 algorithm 的快照，顺序处理 replay/frames.bin，并比较全部历史输出。
Linux 可使用 GCC、CMake 和 Make，脚本会选择相应生成器。

请基于输入、输出与源码定位问题，说明根因和最小修改，并在同一段历史上验证。
探索模式为 {manifest.get('exploratory', False)}，视觉避障为 {manifest.get('visual_avoid', False)}。
MCU 一致性和外部事件重放的限制保留于 experiment.json。
'''
            write("ISSUE.md", summary.encode("utf-8"))
            write("manifest.json", json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8"))
        if sha256(raw) != manifest["raw_sha256"]:
            raise ValueError("原始输入在导出期间发生变化")
        # rename refuses an existing destination on Windows, preserving prior exports.
        temporary.rename(target)
    finally:
        del frames
        temporary.unlink(missing_ok=True)


REPRODUCE = '''"""Build and sequentially reproduce this exported VisionLab experiment."""
import json
import os
from pathlib import Path
import subprocess

root = Path(__file__).absolute().parent
os.chdir(root)
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
generator = "MinGW Makefiles" if os.name == "nt" else "Unix Makefiles"
subprocess.run(["cmake", "-S", str(root / "algorithm"), "-B", str(root / "replay-build"), "-G", generator,
                "-DCMAKE_C_COMPILER=gcc", "-DCMAKE_BUILD_TYPE=Debug",
                "-DCMAKE_DEPENDS_USE_COMPILER=FALSE",
                "-DVISIONLAB_IMAGE_SOURCE=" + str(root / "algorithm" / manifest["algorithm_entry"])], check=True)
subprocess.run(["cmake", "--build", str(root / "replay-build")], check=True)
executable = root / "replay-build" / ("visionlab_replay.exe" if os.name == "nt" else "visionlab_replay")
output = root / "reproduced.jsonl"
if output.exists():
    raise SystemExit("reproduced.jsonl already exists. Choose a fresh extraction directory.")
subprocess.run([str(executable), "replay/frames.bin", str(output),
                str(manifest["replay_prefix"][1]), str(int(manifest["target_detect"]))], check=True)
actual = [json.loads(line) for line in output.read_text().splitlines()]
expected = [json.loads(line) for line in (root / "replay/expected.jsonl").read_text().splitlines()]
if actual != expected:
    raise SystemExit("Output differs from the recorded experiment. Inspect compiler/platform or source changes.")
print("PASS: all prefix outputs match the recorded C experiment")
'''
