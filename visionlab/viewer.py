"""Display-only overlays. The source dataset is always opened read-only."""
import json
from pathlib import Path

import cv2
import numpy as np

from .dataset import load_dataset, sha256


def overlay_pixels(frame, result):
    display = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    colors = {"left": (255, 100, 0), "right": (0, 150, 255),
              "left_control": (255, 255, 0), "right_control": (0, 255, 255), "mid": (0, 255, 0)}
    for name, color in colors.items():
        for row, col in enumerate(result[name]):
            if row >= 9 and 0 <= col < 188:
                display[row, col] = color
    cv2.line(display, (result["reference_col"], 9), (result["reference_col"], 119), (255, 0, 255))
    return display


def overlay(frame, result, frame_id):
    display = overlay_pixels(frame, result)
    display = cv2.resize(display, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
    caption = f'idx={result["index"]} id={frame_id} ring={result["ring_step"]} L={result["ring_l"]} R={result["ring_r"]}'
    cv2.putText(display, caption, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 255), 1)
    return display


def view_run(run: Path, index: int, export: Path | None) -> None:
    manifest = json.loads((run / "run.json").read_text(encoding="utf-8"))
    dataset = Path(manifest["dataset"])
    if sha256(dataset / "frames.npy") != manifest["frames_sha256"]:
        raise ValueError("Dataset changed since this run")
    if sha256(dataset / "frame_ids.npy") != manifest["frame_ids_sha256"]:
        raise ValueError("Frame IDs changed since this run")
    frames, ids, _ = load_dataset(dataset, manifest.get("exploratory", False))
    with (run / "results.jsonl").open(encoding="utf-8") as stream:
        results = [json.loads(line) for line in stream]
    if not 0 <= index < len(results):
        raise ValueError("Index outside completed replay results")
    if any(result["index"] != i for i, result in enumerate(results)):
        raise ValueError("Results must contain contiguous history from index zero")
    if export:
        if export.exists() or export.suffix.lower() != ".png":
            raise ValueError("Overlay export needs a new .png path")
        if not cv2.imwrite(str(export), overlay(frames[index], results[index], int(ids[index]))):
            raise OSError("Failed to write overlay")
        return
    playing = False
    title = "VisionLab | A/D step | SPACE play | Q quit | trackbar seek"
    cv2.namedWindow(title)
    cv2.createTrackbar("index", title, index, max(1, len(results) - 1), lambda _: None)
    try:
        while True:
            index = min(cv2.getTrackbarPos("index", title), len(results) - 1)
            cv2.imshow(title, overlay(frames[index], results[index], int(ids[index])))
            key = cv2.waitKey(60 if playing else 30) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key == ord(" "):
                playing = not playing
            if key == ord("a"):
                index = max(0, index - 1)
            elif key == ord("d") or playing:
                index = min(len(results) - 1, index + 1)
                if index == len(results) - 1:
                    playing = False
            cv2.setTrackbarPos("index", title, index)
    finally:
        cv2.destroyAllWindows()
