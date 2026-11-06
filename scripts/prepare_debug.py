"""Refresh only the designated build/debug session for VS Code F5."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from visionlab.__main__ import DEFAULT_EXE, prepare

parser = argparse.ArgumentParser()
parser.add_argument("dataset", type=Path)
parser.add_argument("through", type=int)
parser.add_argument("--mode", choices=("strict", "exploratory"), default="strict")
args = parser.parse_args()
session = ROOT / "build" / "debug"
if session.exists():
    # Refuse unknown contents, symlinks and input paths before replacing files.
    expected = {"frames.bin", "run.json", "results.jsonl"}
    if session.is_symlink() or args.dataset.resolve().is_relative_to(session.resolve()):
        raise ValueError("Debug session must be separate from the dataset")
    if any(path.name not in expected or not path.is_file() or path.is_symlink() for path in session.iterdir()):
        raise ValueError("Unexpected content in build/debug")
    for path in session.iterdir():
        path.unlink()
    session.rmdir()
prepare(args.dataset, session, args.through, False, DEFAULT_EXE, args.mode == "exploratory")
print(json.dumps({"debug_through_index": args.through, "raw": str(session / "frames.bin")}))
