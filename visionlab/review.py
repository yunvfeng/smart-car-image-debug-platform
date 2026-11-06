"""Frame selections and persistent human annotations."""
import json
from pathlib import Path
import re


def parse_selection(text: str, frame_count: int) -> list[int]:
    indices = set()
    for token in re.split(r"[,，\s]+", text.strip()):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not match:
            raise ValueError(f"无效帧选择 {token}，请使用 12,15,20-24")
        first = int(match[1])
        last = int(match[2]) if match[2] else first
        if first > last or first < 0 or last >= frame_count:
            raise ValueError(f"帧范围超出 0 .. {frame_count - 1}")
        indices.update(range(first, last + 1))
    return sorted(indices)


def selection_ranges(indices: list[int]) -> list[tuple[int, int]]:
    ordered = sorted(set(indices))
    if not ordered:
        return []
    ranges = []
    first = last = ordered[0]
    for index in ordered[1:]:
        if index == last + 1:
            last = index
        else:
            ranges.append((first, last))
            first = last = index
    ranges.append((first, last))
    return ranges


def format_selection(indices: list[int]) -> str:
    return ",".join(str(first) if first == last else f"{first}-{last}"
                    for first, last in selection_ranges(indices))


def load_review(run: Path, frame_count: int) -> dict:
    path = run / "review.json"
    if path.exists():
        review = json.loads(path.read_text(encoding="utf-8"))
        review["selected"] = parse_selection(",".join(map(str, review.get("selected", []))), frame_count)
        review["marks"] = {str(int(index)): value for index, value in review.get("marks", {}).items()
                           if 0 <= int(index) < frame_count}
        return review
    marks = {}
    legacy = run / "annotations.jsonl"
    if legacy.exists():
        for line in legacy.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            index = int(item["index"])
            if 0 <= index < frame_count:
                marks[str(index)] = {"frame_id": item.get("frame_id"), "note": item.get("note", "")}
    return {"schema_version": 1, "marks": marks, "selected": sorted(map(int, marks)), "notes": ""}


def save_review(run: Path, review: dict) -> None:
    temporary = run / "review.tmp"
    temporary.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(run / "review.json")
