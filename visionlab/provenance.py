from pathlib import Path
import os
import re

from .dataset import sha256


def source_files(root: Path, image_source: Path | None = None) -> dict[str, Path]:
    """Resolve the selected entry, companion modules and literal header dependencies."""
    root = root.resolve()
    original = root / "smart-car/project/code"
    entry = (image_source or original / "image.c").resolve()
    if not entry.is_file() or entry.suffix.lower() != ".c":
        raise ValueError(f"Select an existing C algorithm entry file: {entry}")
    search = [root / "visionlab/c/platform", entry.parent, original]
    pending = [root / "visionlab/c/replay.c", root / "visionlab/c/platform.c"]
    names = ("image.c", "image2.c", "ring.c", "visual_avoidance.c", "laser.c")
    selected_name = entry.name if entry.name in names else "image.c"
    for name in names:
        companion = entry.parent / name
        if name == selected_name:
            pending.append(entry)
        elif companion.exists():
            pending.append(companion)
        else:
            pending.append(original / name)
    found = set()
    while pending:
        path = pending.pop().resolve()
        if path in found:
            continue
        found.add(path)
        for delimiter, name in re.findall(rb'^\s*#\s*include\s*(["<])([^">]+)[">]', path.read_bytes(), re.MULTILINE):
            header = name.decode("utf-8")
            directories = [path.parent, *search] if delimiter == b'"' else search
            for directory in directories:
                candidate = directory / header
                if candidate.is_file():
                    pending.append(candidate)
                    break
    def is_external(path: Path) -> bool:
        return not path.is_relative_to(original) and not path.is_relative_to(root / "visionlab/c")
    external = [p for p in found if is_external(p)]
    external_root = Path(os.path.commonpath([str(p.parent) for p in external])) if external else None
    result = {}
    for path in sorted(found):
        key = "external/" + path.relative_to(external_root).as_posix() if is_external(path) else path.relative_to(root).as_posix()
        result[key] = path
    result["CMakeLists.txt"] = root / "CMakeLists.txt"
    return result


def source_hashes(root: Path, image_source: Path | None = None) -> dict[str, str]:
    return {name: sha256(path) for name, path in source_files(root, image_source).items()}
