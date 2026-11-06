"""Read exact camera bytes without dtype conversion or image preprocessing."""
import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_dataset(directory: Path, allow_incomplete: bool = False):
    frames = np.load(directory / "frames.npy", mmap_mode="r", allow_pickle=False)
    ids = np.load(directory / "frame_ids.npy", allow_pickle=False)
    if frames.dtype != np.uint8 or frames.ndim != 3 or frames.shape[1:] != (120, 188):
        raise ValueError("frames.npy must have dtype uint8 and shape (N,120,188)")
    if not len(frames) or ids.shape != (len(frames),) or ids.dtype.kind not in "ui":
        raise ValueError("frame_ids.npy must contain one integer ID per nonempty frame")
    if np.any(ids[1:] <= ids[:-1]):
        raise ValueError("Processed-frame IDs must be strictly increasing, without wrap")
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("metadata.json must be an object")
    if not allow_incomplete and metadata.get("status") in ("recording", "failed"):
        raise ValueError("Recording is incomplete or failed. Exploratory inspection is required")
    if not allow_incomplete and metadata.get("initial_state") != "process_start":
        raise ValueError("Replay requires history from process_start. Mid-session state restoration is not implemented")
    if not allow_incomplete and metadata.get("parameters_changed"):
        raise ValueError("Recorded parameter changes need event replay. Use exploratory mode for inspection")
    if (directory / "events.bin").exists():
        raise ValueError("This phase does not implement events.bin. Event-bearing replay requires an adapter")
    return frames, ids, metadata


def make_demo(directory: Path, count: int) -> None:
    if count < 1:
        raise ValueError("Frame count must be positive")
    directory.mkdir(parents=True, exist_ok=False)
    frames = np.full((count, 120, 188), 25, dtype=np.uint8)
    for index, frame in enumerate(frames):
        offset = int(8 * np.sin(index / 12))
        for row in range(120):
            half_width = 25 + row // 3
            center = 94 + offset
            frame[row, center - half_width:center + half_width] = 175
    np.save(directory / "frames.npy", frames, allow_pickle=False)
    np.save(directory / "frame_ids.npy", np.arange(count, dtype=np.uint64), allow_pickle=False)
    (directory / "metadata.json").write_text(json.dumps({
        "schema_version": 1, "source": "synthetic", "width": 188, "height": 120,
        "description": "Constructed lane for software plumbing tests only",
        "initial_state": "process_start", "external_events": "none",
    }, indent=2), encoding="utf-8")
