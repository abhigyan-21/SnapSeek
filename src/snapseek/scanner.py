"""Discover supported image files directly inside a directory."""

from dataclasses import dataclass
from pathlib import Path
import sys
import stat


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True)
class ImageMetadata:
    """Basic metadata collected for one source image."""

    filename: str
    path: Path
    extension: str
    size_bytes: int


def scan_images(directory: Path) -> list[ImageMetadata]:
    """Return metadata for supported files directly inside ``directory``.

    The scanner only reads file names and sizes; it never changes source files.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"Image directory does not exist: {directory}")

    images = []
    for path in directory.iterdir():
        extension = path.suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            continue
        try:
            file_status = path.stat()
        except FileNotFoundError:
            print(f"Warning: Image no longer exists: {path}", file=sys.stderr)
            continue
        if stat.S_ISREG(file_status.st_mode):
            images.append(
                ImageMetadata(
                    filename=path.name,
                    path=path,
                    extension=extension,
                    size_bytes=file_status.st_size,
                )
            )
    return sorted(images, key=lambda image: image.filename.lower())
