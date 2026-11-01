"""Category-aware ranking and launching for the unified local index."""

from __future__ import annotations

from dataclasses import dataclass
import difflib
import os
from pathlib import Path
import subprocess
import ctypes
import uuid

from snapseek.unified_index import IndexedItem, LocalIndex, PDFSearchResult


@dataclass(frozen=True)
class UnifiedSearchResult:
    item: IndexedItem
    score: float
    result_type: str


def _match_score(query: str, item: IndexedItem) -> float:
    query = query.casefold().strip()
    name = item.name.casefold()
    path = str(item.path).casefold()
    extension = item.extension.casefold()
    if name == query:
        return 1.0
    if name.startswith(query):
        return 0.9
    if query in name:
        return 0.78
    if query in extension or query in path:
        return 0.62
    return 0.45 * difflib.SequenceMatcher(None, query, name).ratio()


def search_index(
    index: LocalIndex, query: str, category: str = "all", limit: int = 30
) -> list[UnifiedSearchResult]:
    """Rank local candidates without comparing screenshot semantic scores."""
    candidates = index.search(query, category, limit)
    ranked = [
        UnifiedSearchResult(item, _match_score(query, item), item.item_type)
        for item in candidates
        if _match_score(query, item) >= 0.35
    ]
    category_priority = {"app": 3, "folder": 2, "file": 1, "screenshot": 0}
    return sorted(
        ranked,
        key=lambda result: (
            -result.score,
            -category_priority.get(result.result_type, 0),
            result.item.name.casefold(),
            str(result.item.path).casefold(),
        ),
    )[:limit]


def search_pdfs(
    index: LocalIndex, query: str, limit: int = 30
) -> list[PDFSearchResult]:
    """Return ranked page-level PDF keyword matches."""
    return index.search_pdfs(query, limit)


_DESKTOP_KNOWN_FOLDER = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
_PUBLIC_DESKTOP_KNOWN_FOLDER = "C4AA340D-F20F-4863-AFEF-F87EF2E6BA25"


class _KnownFolderGuid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


def _known_folder_path(identifier: str) -> Path | None:
    """Resolve a Windows known folder without making non-Windows imports fail."""
    if os.name != "nt":
        return None
    try:
        guid = _KnownFolderGuid.from_buffer_copy(uuid.UUID(identifier).bytes_le)
        path_pointer = ctypes.c_void_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid),
            0,
            None,
            ctypes.byref(path_pointer),
        )
        if result != 0 or not path_pointer.value:
            return None
        value = ctypes.wstring_at(path_pointer.value)
        ctypes.windll.ole32.CoTaskMemFree(path_pointer)
        return Path(value)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _application_roots() -> tuple[Path, ...]:
    values: list[Path] = []
    if os.environ.get("APPDATA"):
        values.append(Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs")
    if os.environ.get("PROGRAMDATA"):
        values.append(Path(os.environ["PROGRAMDATA"]) / "Microsoft/Windows/Start Menu/Programs")

    user_desktop = _known_folder_path(_DESKTOP_KNOWN_FOLDER)
    if user_desktop is None:
        profile = os.environ.get("USERPROFILE")
        user_desktop = (Path(profile) / "Desktop") if profile else (Path.home() / "Desktop")
    values.append(user_desktop)

    public_desktop = _known_folder_path(_PUBLIC_DESKTOP_KNOWN_FOLDER)
    if public_desktop is None and os.environ.get("PUBLIC"):
        public_desktop = Path(os.environ["PUBLIC"]) / "Desktop"
    if public_desktop is not None:
        values.append(public_desktop)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in values:
        normalized = os.path.normcase(os.path.normpath(os.fspath(path)))
        if normalized not in seen:
            seen.add(normalized)
            unique.append(path)
    return tuple(unique)


def default_application_directories() -> tuple[Path, ...]:
    """Return existing Start Menu and Desktop application locations."""
    return tuple(path for path in configured_application_directories() if path.is_dir())


def configured_application_directories() -> tuple[Path, ...]:
    """Return configured Start Menu and Desktop locations, including unavailable roots."""
    return _application_roots()


def recursive_application_directories(
    directories: tuple[Path, ...] | list[Path],
) -> tuple[Path, ...]:
    """Return only the configured Start Menu roots for recursive discovery."""
    start_menu_keys = {
        os.path.normcase(os.path.normpath(os.fspath(path)))
        for path in directories
        if os.path.normcase(os.path.normpath(os.fspath(path))).replace(
            "\\", "/"
        ).endswith("/microsoft/windows/start menu/programs")
    }
    return tuple(
        path
        for path in directories
        if os.path.normcase(os.path.normpath(os.fspath(path))) in start_menu_keys
    )


def application_event_is_relevant(
    source_path: str,
    destination_path: str | None,
    is_directory: bool,
    directories: tuple[Path, ...] | list[Path],
) -> bool:
    """Return whether an application monitor event can change indexed paths."""
    roots = tuple(
        os.path.normcase(os.path.normpath(os.fspath(root)))
        for root in directories
    )
    recursive_roots = {
        os.path.normcase(os.path.normpath(os.fspath(root)))
        for root in recursive_application_directories(directories)
    }

    def relevant(path_text: str) -> bool:
        normalized = os.path.normcase(os.path.normpath(path_text))
        for root in roots:
            separator = os.sep
            prefix = root + separator
            if not normalized.startswith(prefix):
                continue
            relative = normalized[len(prefix) :]
            components = relative.replace("\\", "/").split("/")
            if root in recursive_roots:
                return is_directory or normalized.casefold().endswith(".lnk")
            return len(components) == 1 and normalized.casefold().endswith(".lnk")
        return False

    return relevant(source_path) or (
        destination_path is not None and relevant(destination_path)
    )


def launch_item(item: IndexedItem) -> None:
    """Open a file, folder, or discovered shortcut with Windows defaults."""
    target = item.launch_target or item.path
    if os.name == "nt":
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif item.item_type == "app":
        subprocess.Popen([str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
