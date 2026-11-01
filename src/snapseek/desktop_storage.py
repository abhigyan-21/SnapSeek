"""Local desktop state for the selected folder and OCR cache."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from snapseek.scanner import ImageMetadata


STATE_VERSION = 1
STATE_FILENAME = "state.json"


def state_path() -> Path:
    """Return the per-user state path without writing to the project."""
    app_data = os.environ.get("LOCALAPPDATA")
    root = Path(app_data) if app_data else Path.home() / ".snapseek"
    return root / "SnapSeek" / STATE_FILENAME


def load_state() -> dict[str, Any]:
    """Load state, treating missing or malformed state as empty."""
    try:
        payload = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "folders": {}, "category": "all"}
    if not isinstance(payload, dict) or payload.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "folders": {}, "category": "all"}
    if not isinstance(payload.get("folders"), dict):
        return {"version": STATE_VERSION, "folders": {}, "category": "all"}
    return payload


def saved_category() -> str:
    """Return the last valid unified-search category."""
    category = load_state().get("category")
    return (
        category
        if category in {"screenshots", "files", "documents", "apps", "all"}
        else "all"
    )


def save_category(category: str) -> None:
    """Persist the category toggle without affecting scan caches."""
    if category not in {"screenshots", "files", "documents", "apps", "all"}:
        return
    current = load_state()
    current["category"] = category
    target = state_path()
    temporary = target.with_suffix(".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
    except OSError:
        return


def save_state(
    directory: Path,
    images: tuple[ImageMetadata, ...],
    *,
    records: tuple[tuple[ImageMetadata, str], ...],
) -> None:
    """Persist the folder and successful OCR records atomically."""
    current = load_state()
    folder_key = str(directory.resolve(strict=False)).casefold()
    cached_images: dict[str, dict[str, object]] = {}
    for image, text in records:
        if not isinstance(text, str):
            continue
        try:
            modified_ns = image.path.stat().st_mtime_ns
        except OSError:
            continue
        cached_images[str(image.path.resolve(strict=False)).casefold()] = {
            "size_bytes": image.size_bytes,
            "modified_ns": modified_ns,
            "ocr_text": text,
        }
    current["folders"][folder_key] = {
        "directory": str(directory.resolve(strict=False)),
        "images": cached_images,
    }
    target = state_path()
    temporary = target.with_suffix(".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
    except OSError:
        # State is an optimization; a read-only or unavailable profile must
        # never make scanning fail.
        return


def save_directory(directory: Path) -> None:
    """Remember a selected directory without discarding its OCR cache."""
    current = load_state()
    folder_key = str(directory.resolve(strict=False)).casefold()
    existing = current["folders"].get(folder_key)
    current["folders"][folder_key] = (
        existing
        if isinstance(existing, dict)
        else {"directory": str(directory.resolve(strict=False)), "images": {}}
    )
    target = state_path()
    temporary = target.with_suffix(".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
    except OSError:
        return


def cached_ocr(
    directory: Path,
    images: tuple[ImageMetadata, ...],
) -> dict[Path, str]:
    """Return OCR text for files unchanged since the last successful scan."""
    folder_key = str(directory.resolve(strict=False)).casefold()
    entry = load_state()["folders"].get(folder_key)
    if not isinstance(entry, dict) or not isinstance(entry.get("images"), dict):
        return {}
    result: dict[Path, str] = {}
    for image in images:
        try:
            modified_ns = image.path.stat().st_mtime_ns
        except OSError:
            continue
        cached = entry["images"].get(str(image.path.resolve(strict=False)).casefold())
        if (
            isinstance(cached, dict)
            and cached.get("size_bytes") == image.size_bytes
            and cached.get("modified_ns") == modified_ns
            and isinstance(cached.get("ocr_text"), str)
        ):
            result[image.path] = cached["ocr_text"]
    return result


def saved_directory() -> Path | None:
    """Return the last selected directory if it still exists."""
    folders = load_state()["folders"]
    for entry in reversed(list(folders.values())):
        if not isinstance(entry, dict):
            continue
        value = entry.get("directory")
        directory = Path(value) if isinstance(value, str) else None
        if directory is not None and directory.is_dir():
            return directory
    return None
