"""Record the built executable and source identity after successful compilation."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from visionlab.dataset import sha256
from visionlab.provenance import source_files, source_hashes

parser = argparse.ArgumentParser()
parser.add_argument("executable", type=Path, nargs="?")
parser.add_argument("expected", type=Path, nargs="?")
parser.add_argument("--capture", type=Path)
parser.add_argument("--image-source", type=Path, default=ROOT / "smart-car/project/code/image.c")
args = parser.parse_args()
inputs = source_files(ROOT, args.image_source)
record = {"source_sha256": source_hashes(ROOT, args.image_source),
          "source_files": {name: str(path) for name, path in inputs.items()},
          "algorithm_entry": str(args.image_source.resolve())}
if args.capture:
    args.capture.write_text(json.dumps(record, indent=2), encoding="utf-8")
    raise SystemExit(0)
if json.loads(args.expected.read_text(encoding="utf-8")) != record:
    raise SystemExit("Sources changed during compilation. Save files and rebuild.")
record.update(executable_sha256=sha256(args.executable),
              firmware_commit=subprocess.check_output(
                  ["git", "-C", str(ROOT / "smart-car"), "rev-parse", "HEAD"], text=True).strip(),
              compiler=subprocess.check_output(["gcc", "--version"], text=True).splitlines()[0])
args.executable.with_suffix(".build.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
