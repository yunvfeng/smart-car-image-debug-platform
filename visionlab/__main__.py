import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import hashlib

from .dataset import load_dataset, make_demo, sha256
from .provenance import source_hashes

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "build" / "pc" / ("visionlab_replay.exe" if os.name == "nt" else "visionlab_replay")


def prepare(dataset: Path, run: Path, through: int | None, target: bool, executable: Path,
            allow_incomplete: bool = False):
    dataset = dataset.resolve()
    frames, ids, metadata = load_dataset(dataset, allow_incomplete)
    if through is None:
        through = len(frames) - 1
    if not 0 <= through < len(frames):
        raise ValueError("Requested index outside dataset")
    # A gap represents missing state-machine history. Reject it for strict replay.
    if not allow_incomplete and any(int(ids[i + 1]) != int(ids[i]) + 1 for i in range(through)):
        raise ValueError("Processed-frame ID gap. Missing history prevents strict replay")
    build = json.loads(executable.with_suffix(".build.json").read_text(encoding="utf-8"))
    if not build.get("source_files"):
        raise ValueError("Build provenance needs source snapshots. Rebuild the C executable first")
    entry = Path(build.get("algorithm_entry", ROOT / "smart-car/project/code/image.c"))
    if build["executable_sha256"] != sha256(executable) or build["source_sha256"] != source_hashes(ROOT, entry):
        raise ValueError("Executable or sources changed. Rebuild before preparing replay")
    frames_hash = sha256(dataset / "frames.npy")
    ids_hash = sha256(dataset / "frame_ids.npy")
    run.mkdir(parents=True, exist_ok=False)
    # Freeze the exact source bytes used by this experiment for later AI export.
    for name, source in build["source_files"].items():
        data = Path(source).read_bytes()
        if hashlib.sha256(data).hexdigest() != build["source_sha256"][name]:
            raise ValueError("Algorithm changed while preparing its source snapshot")
        destination = run / "algorithm" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    raw = run / "frames.bin"
    frames[:through + 1].tofile(raw)
    if frames_hash != sha256(dataset / "frames.npy") or ids_hash != sha256(dataset / "frame_ids.npy"):
        raise ValueError("Dataset changed while preparing replay")
    command = [str(executable.resolve()), str(raw.resolve()), str((run / "results.jsonl").resolve()),
               str(through), str(int(target))]
    manifest = {
        "schema_version": 1, "status": "prepared", "dataset": str(dataset),
        "frames_sha256": frames_hash,
        "frame_ids_sha256": ids_hash,
        "raw_sha256": sha256(raw), "metadata": metadata,
        "frame_ids": [int(value) for value in ids[:through + 1]],
        "through_index": through, "target_detect": target, "visual_avoid": False,
        "initial_state": "fresh_process", "firmware_commit": build["firmware_commit"],
        "exploratory": allow_incomplete,
        "source_sha256": build["source_sha256"], "compiler": build["compiler"], "command": command,
        "algorithm_entry": build["algorithm_entry"], "source_files": build["source_files"],
        "source_snapshot": "algorithm",
        "executable_sha256": sha256(executable) if executable.exists() else None,
        "limitations": ["No MCU equivalence established", "No external-event replay"],
    }
    write_manifest(run, manifest)
    return manifest


def write_manifest(run: Path, manifest: dict) -> None:
    (run / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="VisionLab original C replay")
    commands = parser.add_subparsers(dest="action", required=True)
    demo = commands.add_parser("demo")
    demo.add_argument("dataset", type=Path)
    demo.add_argument("--frames", type=int, default=60)
    for action in ("prepare", "run"):
        cmd = commands.add_parser(action)
        cmd.add_argument("dataset", type=Path)
        cmd.add_argument("output", type=Path)
        cmd.add_argument("--through", type=int, help="zero-based index, executes ALL preceding frames")
        cmd.add_argument("--target-detect", action="store_true")
        cmd.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    view = commands.add_parser("view")
    view.add_argument("run", type=Path)
    view.add_argument("--index", type=int, default=0)
    view.add_argument("--export", type=Path, help="export overlay PNG without opening a window")
    args = parser.parse_args()
    if args.action == "demo":
        make_demo(args.dataset, args.frames)
    elif args.action == "view":
        from .viewer import view_run
        view_run(args.run, args.index, args.export)
    else:
        if not args.exe.is_file():
            raise ValueError(f"Build the C executable first: {args.exe}")
        manifest = prepare(args.dataset, args.output, args.through, args.target_detect, args.exe)
        if args.action == "run":
            result = subprocess.run(manifest["command"], check=False)
            manifest["status"] = "complete" if result.returncode == 0 else "failed"
            manifest["exit_code"] = result.returncode
            if result.returncode == 0:
                manifest["results_sha256"] = sha256(args.output / "results.jsonl")
            write_manifest(args.output, manifest)
            if result.returncode:
                raise RuntimeError(f"C replay failed with exit code {result.returncode}")
        print(args.output.resolve())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"VisionLab: {error}", file=sys.stderr)
        sys.exit(1)
