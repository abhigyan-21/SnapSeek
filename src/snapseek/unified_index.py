"""Local SQLite index for files, folders, applications, and screenshots."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Callable, Iterable

from snapseek.pdf_extractor import (
    PDFExtractionCancelled,
    PDFExtractionResult,
    PDFExtractionStatus,
    extract_pdf_text,
    has_meaningful_text,
)
from snapseek.ocr import ImageOCRError, OCRError, extract_text_from_image
from snapseek.pdf_renderer import (
    PDFRenderCancelled,
    PDFRenderError,
    PDFRenderLimitError,
    PDFRenderer,
)
from snapseek.scanner import SUPPORTED_EXTENSIONS


SCHEMA_VERSION = 4
DEFAULT_PDF_OCR_PAGE_LIMIT = 200
DEFAULT_EXCLUDED_DIRECTORIES = frozenset(
    {".git", ".venv", "__pycache__", "build", "dist", "node_modules", ".cache"}
)


class FileIntegrityDeferred(Exception):
    """The file could not yet be safely read for indexing."""


_TRANSIENT_FILE_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EAGAIN,
        errno.EBUSY,
        errno.EIO,
        errno.EMFILE,
        errno.ENFILE,
        errno.ETXTBSY,
    }
)


def _is_transient_file_error(error: OSError) -> bool:
    return isinstance(error, (PermissionError, BlockingIOError)) or (
        error.errno in _TRANSIENT_FILE_ERRNOS
    )


@dataclass(frozen=True)
class IndexedItem:
    """A searchable local item."""

    item_type: str
    name: str
    path: Path
    extension: str
    launch_target: Path | None = None
    ocr_text: str = ""


@dataclass(frozen=True)
class PDFIndexOutcome:
    """Result of an incremental PDF persistence operation."""

    action: str
    path: Path
    status: PDFExtractionStatus | None = None
    error: str | None = None


@dataclass(frozen=True)
class PDFSearchResult:
    """A page-level keyword match from a persisted PDF."""

    path: Path
    name: str
    title: str | None
    page_number: int
    snippet: str
    score: float
    extraction_status: str
    matched_terms: tuple[str, ...] = ()


class LocalIndex:
    """Versioned SQLite index stored in the user's local application data."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._pdf_fts_available = False
        self._pdf_generations: dict[str, int] = {}
        self._migrate()

    def close(self) -> None:
        with self._lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None  # type: ignore[assignment]
            self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except (AttributeError, sqlite3.Error):
            pass

    def __enter__(self) -> "LocalIndex":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = self.connection.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()
        version = int(row["version"]) if row else 0
        if version > SCHEMA_VERSION:
            raise RuntimeError("The SnapSeek index was created by a newer version.")
        if version < 1:
            self.connection.executescript(
                """
                CREATE TABLE roots (
                    path TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    indexed_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT
                );
                CREATE TABLE items (
                    path TEXT PRIMARY KEY,
                    item_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    parent_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    launch_target TEXT,
                    ocr_text TEXT NOT NULL DEFAULT '',
                    embedding BLOB,
                    indexed_at REAL NOT NULL
                );
                CREATE INDEX items_name_idx ON items(name COLLATE NOCASE);
                CREATE INDEX items_type_idx ON items(item_type);
                CREATE INDEX items_parent_idx ON items(parent_path);
                DELETE FROM schema_version;
                INSERT INTO schema_version(version) VALUES (1);
                """
            )
            version = 1
        if version < 2:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pdf_documents (
                    path TEXT PRIMARY KEY,
                    size_bytes INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    title TEXT,
                    author TEXT,
                    page_count INTEGER NOT NULL,
                    extraction_status TEXT NOT NULL,
                    extraction_error TEXT,
                    indexed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pdf_pages (
                    document_path TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    has_text INTEGER NOT NULL,
                    PRIMARY KEY (document_path, page_number)
                );
                CREATE INDEX IF NOT EXISTS pdf_pages_document_idx
                    ON pdf_pages(document_path);
                CREATE INDEX IF NOT EXISTS pdf_documents_status_idx
                    ON pdf_documents(extraction_status);
                UPDATE schema_version SET version = 2;
                """
            )
            version = 2
        if version < 3:
            try:
                self.connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS pdf_pages_fts
                    USING fts5(
                        document_path UNINDEXED,
                        page_number UNINDEXED,
                        text
                    )
                    """
                )
                self._pdf_fts_available = True
                self.connection.execute(
                    """
                    INSERT INTO pdf_pages_fts(document_path, page_number, text)
                    SELECT p.document_path, p.page_number, p.text
                    FROM pdf_pages AS p
                    JOIN pdf_documents AS d ON d.path = p.document_path
                    WHERE d.extraction_status = 'ok'
                    """
                )
            except sqlite3.OperationalError:
                self._pdf_fts_available = False
            self.connection.execute("UPDATE schema_version SET version = 3")
            version = 3
        if version < 4:
            columns = {
                str(row["name"])
                for row in self.connection.execute(
                    "PRAGMA table_info(pdf_pages)"
                ).fetchall()
            }
            if "extracted_text" not in columns:
                self.connection.execute(
                    "ALTER TABLE pdf_pages ADD COLUMN extracted_text TEXT NOT NULL DEFAULT ''"
                )
            if "ocr_text" not in columns:
                self.connection.execute(
                    "ALTER TABLE pdf_pages ADD COLUMN ocr_text TEXT NOT NULL DEFAULT ''"
                )
            if "text_source" not in columns:
                self.connection.execute(
                    "ALTER TABLE pdf_pages ADD COLUMN text_source TEXT NOT NULL DEFAULT 'none'"
                )
            self.connection.execute(
                """
                UPDATE pdf_pages
                SET extracted_text = CASE WHEN has_text = 1 THEN text ELSE '' END,
                    text_source = CASE WHEN has_text = 1 THEN 'extracted' ELSE 'none' END
                """
            )
            if self._pdf_fts_available:
                self.connection.execute("DELETE FROM pdf_pages_fts")
                self.connection.execute(
                    """
                    INSERT INTO pdf_pages_fts(document_path, page_number, text)
                    SELECT p.document_path, p.page_number, p.text
                    FROM pdf_pages AS p
                    JOIN pdf_documents AS d ON d.path = p.document_path
                    WHERE d.extraction_status = 'ok' AND p.has_text = 1
                    """
                )
            self.connection.execute("UPDATE schema_version SET version = 4")
            version = 4
        if version >= 3:
            try:
                self.connection.execute(
                    "SELECT 1 FROM pdf_pages_fts LIMIT 1"
                )
                self._pdf_fts_available = True
            except sqlite3.OperationalError:
                self._pdf_fts_available = False
        self.connection.commit()

    @staticmethod
    def _key(path: Path) -> str:
        return str(path.expanduser().resolve(strict=False)).casefold()

    def add_root(self, path: str | Path, *, kind: str = "folder") -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            root = Path(path).expanduser().resolve(strict=False)
            self.connection.execute(
                """
                INSERT INTO roots(path, kind, indexed_at, status, error)
                VALUES (?, ?, ?, 'pending', NULL)
                ON CONFLICT(path) DO UPDATE SET kind=excluded.kind
                """,
                (self._key(root), kind, time.time()),
            )
            self.connection.commit()

    @staticmethod
    def _pdf_path(path: str | Path) -> Path:
        return Path(path).expanduser().resolve(strict=False)

    def _pdf_fingerprint(
        self,
        path: Path,
        *,
        cancelled: Callable[[], bool],
    ) -> tuple[int, int, str]:
        """Return metadata and a bounded-memory SHA-256 fingerprint."""
        try:
            initial = path.stat()
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while True:
                    if cancelled():
                        raise PDFExtractionCancelled("PDF indexing was cancelled.")
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            final = path.stat()
        except PDFExtractionCancelled:
            raise
        except OSError as error:
            if _is_transient_file_error(error):
                raise FileIntegrityDeferred(str(error)) from error
            raise
        if (
            initial.st_size != final.st_size
            or initial.st_mtime_ns != final.st_mtime_ns
        ):
            raise FileIntegrityDeferred(f"PDF changed while being read: {path}")
        return final.st_size, final.st_mtime_ns, digest.hexdigest()

    def _pdf_record(self, key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT size_bytes, modified_ns, content_hash, extraction_status
            FROM pdf_documents WHERE path = ?
            """,
            (key,),
        ).fetchone()

    def _next_pdf_generation_locked(self, key: str) -> int:
        generation = self._pdf_generations.get(key, 0) + 1
        self._pdf_generations[key] = generation
        return generation

    def _pdf_generation_current_locked(self, key: str, generation: int) -> bool:
        return self._pdf_generations.get(key, 0) == generation

    def _delete_pdf_records_locked(self, keys: Iterable[str]) -> int:
        normalized = tuple(dict.fromkeys(keys))
        if not normalized:
            return 0
        placeholders = ",".join("?" for _ in normalized)
        self.connection.execute(
            f"DELETE FROM pdf_pages WHERE document_path IN ({placeholders})",
            normalized,
        )
        if self._pdf_fts_available:
            self.connection.execute(
                f"DELETE FROM pdf_pages_fts WHERE document_path IN ({placeholders})",
                normalized,
            )
        cursor = self.connection.execute(
            f"DELETE FROM pdf_documents WHERE path IN ({placeholders})",
            normalized,
        )
        return cursor.rowcount

    def _delete_pdf_records_under_locked(
        self, prefix: str, *, keep: set[str] | None = None
    ) -> int:
        rows = self.connection.execute(
            """
            SELECT path FROM pdf_documents
            WHERE path = ? OR path LIKE ?
            """,
            (prefix, prefix + os.sep + "%"),
        ).fetchall()
        stale = [
            str(row["path"])
            for row in rows
            if keep is None or str(row["path"]) not in keep
        ]
        return self._delete_pdf_records_locked(stale)

    def index_pdf(
        self,
        path: str | Path,
        cancelled: Callable[[], bool] | None = None,
        *,
        force_integrity_check: bool = False,
    ) -> PDFIndexOutcome:
        """Incrementally extract and persist one PDF without exposing it to search."""
        return self._index_pdf_stage94(
            path,
            cancelled=cancelled,
            force_integrity_check=force_integrity_check,
        )

    def _index_pdf_stage94(
        self,
        path: str | Path,
        cancelled: Callable[[], bool] | None = None,
        *,
        force_integrity_check: bool = False,
        ocr_page_limit: int = DEFAULT_PDF_OCR_PAGE_LIMIT,
    ) -> PDFIndexOutcome:
        """Prepare PDF/OCR data outside SQLite, then replace it atomically."""
        is_cancelled = cancelled or (lambda: False)
        candidate = self._pdf_path(path)
        key = self._key(candidate)
        if is_cancelled():
            raise PDFExtractionCancelled("PDF indexing was cancelled.")
        if candidate.suffix.casefold() != ".pdf":
            return PDFIndexOutcome(
                "status-only", candidate, PDFExtractionStatus.UNSUPPORTED,
                "The file does not have a .pdf extension.",
            )
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            generation = self._next_pdf_generation_locked(key)
            previous = self._pdf_record(key)
            try:
                stat = candidate.stat()
                if not candidate.is_file():
                    raise OSError("The PDF path is not a regular file.")
            except OSError as error:
                if previous is not None:
                    return PDFIndexOutcome("deferred", candidate, error=str(error))
                return PDFIndexOutcome(
                    "status-only", candidate, PDFExtractionStatus.INACCESSIBLE,
                    str(error),
                )
            metadata_matches = (
                previous is not None
                and int(previous["size_bytes"]) == stat.st_size
                and int(previous["modified_ns"]) == stat.st_mtime_ns
            )
            if metadata_matches and not force_integrity_check:
                return PDFIndexOutcome(
                    "skipped", candidate,
                    PDFExtractionStatus(str(previous["extraction_status"])),
                )

        try:
            size_bytes, modified_ns, content_hash = self._pdf_fingerprint(
                candidate, cancelled=is_cancelled
            )
        except PDFExtractionCancelled:
            raise
        except (FileIntegrityDeferred, OSError) as error:
            return PDFIndexOutcome("deferred", candidate, error=str(error))

        if (
            previous is not None
            and int(previous["size_bytes"]) == size_bytes
            and int(previous["modified_ns"]) == modified_ns
            and str(previous["content_hash"]) == content_hash
        ):
            return PDFIndexOutcome(
                "skipped", candidate,
                PDFExtractionStatus(str(previous["extraction_status"])),
            )

        result = extract_pdf_text(candidate, cancelled=is_cancelled)
        if result.status == PDFExtractionStatus.INACCESSIBLE:
            return PDFIndexOutcome("deferred", candidate, result.status, result.error)
        if result.status in {
            PDFExtractionStatus.ENCRYPTED,
            PDFExtractionStatus.CORRUPT,
            PDFExtractionStatus.UNSUPPORTED,
            PDFExtractionStatus.EMPTY,
        }:
            prepared_pages = tuple(
                (page.page_number, page.text, int(page.has_text), "extracted", "")
                for page in result.pages
            )
            final_status = result.status
            final_error = result.error
        else:
            prepared: list[tuple[int, str, int, str, str]] = []
            ocr_pages = 0
            renderer = None
            try:
                for page in result.pages:
                    if is_cancelled():
                        raise PDFExtractionCancelled("PDF indexing was cancelled.")
                    if page.has_text and has_meaningful_text(page.text):
                        prepared.append(
                            (page.page_number, page.text, 1, "extracted", "")
                        )
                        continue
                    if ocr_pages >= ocr_page_limit:
                        raise PDFRenderError(
                            f"OCR page limit ({ocr_page_limit}) reached."
                        )
                    if renderer is None:
                        renderer = PDFRenderer(
                            candidate,
                            cancelled=is_cancelled,
                        )
                        renderer.__enter__()
                    ocr_pages += 1
                    image = renderer.render_page(page.page_number)
                    try:
                        ocr_text = extract_text_from_image(image, timeout=30)
                    finally:
                        image.close()
                    if has_meaningful_text(ocr_text):
                        prepared.append(
                            (page.page_number, ocr_text, 1, "ocr", "")
                        )
                    else:
                        prepared.append(
                            (page.page_number, "", 0, "none", "")
                        )
            except PDFExtractionCancelled:
                raise
            except (PDFRenderError, OCRError, OSError) as error:
                with self._lock:
                    if previous is not None:
                        return PDFIndexOutcome("deferred", candidate, error=str(error))
                    return self._persist_pdf_status_only_locked(
                        candidate,
                        key,
                        size_bytes,
                        modified_ns,
                        content_hash,
                        result,
                        error=f"OCR incomplete: {error}",
                        generation=generation,
                    )
            finally:
                if renderer is not None:
                    renderer.__exit__(None, None, None)
            prepared_pages = tuple(prepared)
            final_status = (
                PDFExtractionStatus.OK
                if any(row[2] for row in prepared_pages)
                else PDFExtractionStatus.OCR_REQUIRED
            )
            final_error = (
                None
                if final_status == PDFExtractionStatus.OK
                else "No meaningful selectable or OCR text was produced."
            )

        try:
            end_size, end_modified, end_hash = self._pdf_fingerprint(
                candidate, cancelled=is_cancelled
            )
        except (FileIntegrityDeferred, OSError) as error:
            return PDFIndexOutcome("deferred", candidate, error=str(error))
        if (end_size, end_modified, end_hash) != (
            size_bytes, modified_ns, content_hash
        ):
            return PDFIndexOutcome(
                "deferred", candidate, error="PDF changed during indexing."
            )
        with self._lock:
            if not self._pdf_generation_current_locked(key, generation):
                return PDFIndexOutcome(
                    "deferred", candidate, error="A newer PDF indexing request superseded this result."
                )
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            savepoint = "pdf_index_replace_stage94"
            had_transaction = self.connection.in_transaction
            self.connection.execute(f"SAVEPOINT {savepoint}")
            try:
                self.connection.execute(
                    """
                    INSERT INTO items(
                        path, item_type, name, extension, parent_path,
                        size_bytes, modified_ns, launch_target, ocr_text,
                        embedding, indexed_at
                    ) VALUES (?, 'file', ?, '.pdf', ?, ?, ?, NULL, '', NULL, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        item_type='file', name=excluded.name,
                        extension='.pdf', parent_path=excluded.parent_path,
                        size_bytes=excluded.size_bytes,
                        modified_ns=excluded.modified_ns,
                        launch_target=NULL, ocr_text='', indexed_at=excluded.indexed_at
                    """,
                    (key, candidate.name, self._key(candidate.parent),
                     end_size, end_modified, time.time()),
                )
                self._delete_pdf_records_locked((key,))
                self.connection.execute(
                    """
                    INSERT INTO pdf_documents(
                        path, size_bytes, modified_ns, content_hash, title,
                        author, page_count, extraction_status,
                        extraction_error, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (key, end_size, end_modified, end_hash, result.metadata.title,
                     result.metadata.author, result.metadata.page_count,
                     str(final_status), final_error, time.time()),
                )
                self.connection.executemany(
                    """
                    INSERT INTO pdf_pages(
                        document_path, page_number, text, has_text,
                        extracted_text, ocr_text, text_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            key, page_number, text, has_text,
                            text if source == "extracted" else "",
                            text if source == "ocr" else "",
                            source,
                        )
                        for page_number, text, has_text, source, _error in prepared_pages
                    ),
                )
                if self._pdf_fts_available and final_status == PDFExtractionStatus.OK:
                    self.connection.executemany(
                        """
                        INSERT INTO pdf_pages_fts(document_path, page_number, text)
                        VALUES (?, ?, ?)
                        """,
                        (
                            (key, page_number, text)
                            for page_number, text, has_text, _source, _error in prepared_pages
                            if has_text
                        ),
                    )
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            if not had_transaction:
                self.connection.commit()
        return PDFIndexOutcome("indexed", candidate, final_status, final_error)

    def _persist_pdf_status_only_locked(
        self,
        candidate: Path,
        key: str,
        size_bytes: int,
        modified_ns: int,
        content_hash: str,
        result: PDFExtractionResult,
        *,
        error: str,
        generation: int,
    ) -> PDFIndexOutcome:
        if not self._pdf_generation_current_locked(key, generation):
            return PDFIndexOutcome("deferred", candidate, error=error)
        self._delete_pdf_records_locked((key,))
        self.connection.execute(
            """
            INSERT INTO pdf_documents(
                path, size_bytes, modified_ns, content_hash, title, author,
                page_count, extraction_status, extraction_error, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key, size_bytes, modified_ns, content_hash, result.metadata.title,
                result.metadata.author, result.metadata.page_count,
                PDFExtractionStatus.OCR_REQUIRED.value, error, time.time(),
            ),
        )
        self.connection.commit()
        return PDFIndexOutcome(
            "status-only", candidate, PDFExtractionStatus.OCR_REQUIRED, error
        )

    def _legacy_index_pdf_unreachable(
        self,
        path: str | Path,
        cancelled: Callable[[], bool] | None = None,
        *,
        force_integrity_check: bool = False,
    ) -> PDFIndexOutcome:
        """Retained only as a source-compatible marker for the old implementation."""
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            is_cancelled = cancelled or (lambda: False)
            candidate = self._pdf_path(path)
            key = self._key(candidate)
            if is_cancelled():
                raise PDFExtractionCancelled("PDF indexing was cancelled.")
            if candidate.suffix.casefold() != ".pdf":
                return PDFIndexOutcome(
                    "status-only", candidate, PDFExtractionStatus.UNSUPPORTED,
                    "The file does not have a .pdf extension.",
                )
            try:
                stat = candidate.stat()
                if not candidate.is_file():
                    raise OSError("The PDF path is not a regular file.")
            except OSError as error:
                previous = self._pdf_record(key)
                if previous is not None and _is_transient_file_error(error):
                    return PDFIndexOutcome("deferred", candidate, error=str(error))
                if previous is not None:
                    return PDFIndexOutcome("deferred", candidate, error=str(error))
                return PDFIndexOutcome(
                    "status-only", candidate, PDFExtractionStatus.INACCESSIBLE,
                    str(error),
                )

            previous = self._pdf_record(key)
            metadata_matches = (
                previous is not None
                and int(previous["size_bytes"]) == stat.st_size
                and int(previous["modified_ns"]) == stat.st_mtime_ns
            )
            if metadata_matches and not force_integrity_check:
                return PDFIndexOutcome(
                    "skipped", candidate,
                    PDFExtractionStatus(str(previous["extraction_status"])),
                )

            try:
                size_bytes, modified_ns, content_hash = self._pdf_fingerprint(
                    candidate, cancelled=is_cancelled
                )
            except PDFExtractionCancelled:
                raise
            except FileIntegrityDeferred as error:
                return PDFIndexOutcome("deferred", candidate, error=str(error))
            except OSError as error:
                if previous is not None:
                    return PDFIndexOutcome("deferred", candidate, error=str(error))
                return PDFIndexOutcome(
                    "status-only", candidate, PDFExtractionStatus.INACCESSIBLE,
                    str(error),
                )

            if (
                previous is not None
                and int(previous["size_bytes"]) == size_bytes
                and int(previous["modified_ns"]) == modified_ns
                and str(previous["content_hash"]) == content_hash
            ):
                return PDFIndexOutcome(
                    "skipped", candidate,
                    PDFExtractionStatus(str(previous["extraction_status"])),
                )

            result = extract_pdf_text(candidate, cancelled=is_cancelled)
            if is_cancelled():
                raise PDFExtractionCancelled("PDF indexing was cancelled.")
            try:
                end_size, end_modified, end_hash = self._pdf_fingerprint(
                    candidate, cancelled=is_cancelled
                )
            except (FileIntegrityDeferred, OSError) as error:
                return PDFIndexOutcome("deferred", candidate, error=str(error))
            if (end_size, end_modified, end_hash) != (
                size_bytes, modified_ns, content_hash
            ):
                return PDFIndexOutcome(
                    "deferred", candidate, error="PDF changed during extraction."
                )

            if result.status == PDFExtractionStatus.INACCESSIBLE:
                return PDFIndexOutcome(
                    "deferred", candidate, result.status, result.error
                )

            savepoint = "pdf_index_replace"
            had_transaction = self.connection.in_transaction
            self.connection.execute(f"SAVEPOINT {savepoint}")
            try:
                self.connection.execute(
                    """
                    INSERT INTO items(
                        path, item_type, name, extension, parent_path,
                        size_bytes, modified_ns, launch_target, ocr_text,
                        embedding, indexed_at
                    ) VALUES (?, 'file', ?, '.pdf', ?, ?, ?, NULL, '', NULL, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        item_type='file', name=excluded.name,
                        extension='.pdf', parent_path=excluded.parent_path,
                        size_bytes=excluded.size_bytes,
                        modified_ns=excluded.modified_ns,
                        launch_target=NULL, ocr_text='', indexed_at=excluded.indexed_at
                    """,
                    (
                        key,
                        candidate.name,
                        self._key(candidate.parent),
                        end_size,
                        end_modified,
                        time.time(),
                    ),
                )
                self._delete_pdf_records_locked((key,))
                self.connection.execute(
                    """
                    INSERT INTO pdf_documents(
                        path, size_bytes, modified_ns, content_hash, title,
                        author, page_count, extraction_status,
                        extraction_error, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        end_size,
                        end_modified,
                        end_hash,
                        result.metadata.title,
                        result.metadata.author,
                        result.metadata.page_count,
                        str(result.status),
                        result.error,
                        time.time(),
                    ),
                )
                searchable_pages = (
                    result.pages
                    if result.status in {
                        PDFExtractionStatus.OK,
                        PDFExtractionStatus.EMPTY,
                        PDFExtractionStatus.OCR_REQUIRED,
                    }
                    else ()
                )
                self.connection.executemany(
                    """
                    INSERT INTO pdf_pages(
                        document_path, page_number, text, has_text
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        (key, page.page_number, page.text, int(page.has_text))
                        for page in searchable_pages
                    ),
                )
                if self._pdf_fts_available:
                    self.connection.executemany(
                        """
                        INSERT INTO pdf_pages_fts(
                            document_path, page_number, text
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            (key, page.page_number, page.text)
                            for page in searchable_pages
                            if page.has_text
                        ),
                    )
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            if not had_transaction:
                self.connection.commit()
            return PDFIndexOutcome("indexed", candidate, result.status, result.error)

    def index_folder(
        self,
        path: str | Path,
        *,
        ocr_extractor: Callable[[Path], str] | None = None,
        cancelled: Callable[[], bool] | None = None,
        excluded_directories: Iterable[str] = DEFAULT_EXCLUDED_DIRECTORIES,
    ) -> int:
        """Incrementally index a folder and remove entries deleted below it."""
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            root = Path(path).expanduser().resolve(strict=False)
            if not root.is_dir():
                raise FileNotFoundError(f"Index root does not exist: {root}")
            is_cancelled = cancelled or (lambda: False)
            excluded = {name.casefold() for name in excluded_directories}
            self.add_root(root)
            seen: set[str] = set()
            count = 0
            try:
                for current, directories, filenames in os.walk(root):
                    directories[:] = [
                        name for name in directories if name.casefold() not in excluded
                    ]
                    current_path = Path(current)
                    for directory_name in directories:
                        directory_path = current_path / directory_name
                        key = self._key(directory_path)
                        seen.add(key)
                        try:
                            stat = directory_path.stat()
                        except OSError:
                            continue
                        self.connection.execute(
                            """
                            INSERT INTO items(
                                path, item_type, name, extension, parent_path,
                                size_bytes, modified_ns, launch_target, ocr_text,
                                embedding, indexed_at
                            ) VALUES (?, 'folder', ?, '', ?, 0, ?, NULL, '', NULL, ?)
                            ON CONFLICT(path) DO UPDATE SET
                                name=excluded.name, parent_path=excluded.parent_path,
                                modified_ns=excluded.modified_ns, indexed_at=excluded.indexed_at
                            """,
                            (key, directory_name, self._key(current_path), stat.st_mtime_ns, time.time()),
                        )
                        count += 1
                    for filename in filenames:
                        if is_cancelled():
                            raise RuntimeError("Indexing was cancelled.")
                        candidate = current_path / filename
                        try:
                            stat = candidate.stat()
                        except OSError:
                            continue
                        key = self._key(candidate)
                        seen.add(key)
                        extension = candidate.suffix.casefold()
                        if extension == ".pdf":
                            self.index_pdf(candidate, cancelled=is_cancelled)
                            count += 1
                            continue
                        extension = candidate.suffix.casefold()
                        item_type = "screenshot" if extension in SUPPORTED_EXTENSIONS else "file"
                        previous = self.connection.execute(
                            "SELECT size_bytes, modified_ns, ocr_text FROM items WHERE path = ?",
                            (key,),
                        ).fetchone()
                        text = (
                            str(previous["ocr_text"])
                            if previous
                            and previous["size_bytes"] == stat.st_size
                            and previous["modified_ns"] == stat.st_mtime_ns
                            else ""
                        )
                        if item_type == "screenshot" and not text and ocr_extractor:
                            text = ocr_extractor(candidate)
                        self.connection.execute(
                            """
                            INSERT INTO items(
                                path, item_type, name, extension, parent_path,
                                size_bytes, modified_ns, launch_target, ocr_text,
                                embedding, indexed_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?)
                            ON CONFLICT(path) DO UPDATE SET
                                item_type=excluded.item_type, name=excluded.name,
                                extension=excluded.extension, parent_path=excluded.parent_path,
                                size_bytes=excluded.size_bytes, modified_ns=excluded.modified_ns,
                                ocr_text=excluded.ocr_text, indexed_at=excluded.indexed_at
                            """,
                            (
                                key,
                                item_type,
                                candidate.name,
                                extension,
                                self._key(current_path),
                                stat.st_size,
                                stat.st_mtime_ns,
                                text,
                                time.time(),
                            ),
                        )
                        count += 1
                prefix = self._key(root)
                self.connection.execute(
                    "DELETE FROM items WHERE (path = ? OR path LIKE ?) AND path NOT IN ({})".format(
                        ",".join("?" for _ in seen) or "?"
                    ),
                    (prefix, prefix + os.sep + "%", *seen)
                    if seen
                    else (prefix, prefix + os.sep + "%", ""),
                )
                self._delete_pdf_records_under_locked(prefix, keep=seen)
                self.connection.execute(
                    "UPDATE roots SET indexed_at=?, status='complete', error=NULL WHERE path=?",
                    (time.time(), prefix),
                )
                self.connection.commit()
            except Exception as error:
                self.connection.execute(
                    "UPDATE roots SET status='failed', error=?, indexed_at=? WHERE path=?",
                    (str(error), time.time(), self._key(root)),
                )
                self.connection.commit()
                raise
            return count

    def index_applications(
        self,
        directories: Iterable[str | Path],
        *,
        cancelled: Callable[[], bool] | None = None,
        recursive_directories: Iterable[str | Path] | None = None,
    ) -> int:
        """Refresh application shortcuts without resolving or executing them.

        Only stale application records beneath the supplied roots are removed.
        Other indexed item types and application records outside those roots
        remain untouched. Roots not listed in ``recursive_directories`` are
        searched only at their top level.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            is_cancelled = cancelled or (lambda: False)
            roots = tuple(
                Path(directory).expanduser().resolve(strict=False)
                for directory in directories
            )
            recursive_keys = {
                self._key(Path(directory).expanduser().resolve(strict=False))
                for directory in (recursive_directories or roots)
            }
            enumerated_root_keys: list[str] = []
            discovered: dict[str, tuple[str, int, int, str, str]] = {}
            count = 0
            for root in roots:
                if is_cancelled():
                    raise RuntimeError("Application indexing was cancelled.")
                if not root.is_dir():
                    continue
                try:
                    shortcuts = (
                        root.rglob("*.lnk")
                        if self._key(root) in recursive_keys
                        else root.glob("*.lnk")
                    )
                    for shortcut in shortcuts:
                        if is_cancelled():
                            raise RuntimeError("Application indexing was cancelled.")
                        try:
                            stat = shortcut.stat()
                        except OSError:
                            continue
                        key = self._key(shortcut)
                        discovered[key] = (
                            shortcut.stem,
                            stat.st_size,
                            stat.st_mtime_ns,
                            self._key(shortcut.parent),
                            str(shortcut),
                        )
                except OSError:
                    LOGGER.warning("Could not enumerate application root: %s", root)
                    continue
                enumerated_root_keys.append(self._key(root))
            try:
                self.connection.execute("BEGIN")
                for key, (
                    name,
                    size_bytes,
                    modified_ns,
                    parent_path,
                    launch_target,
                ) in discovered.items():
                    self.connection.execute(
                        """
                        INSERT INTO items(
                            path, item_type, name, extension, parent_path,
                            size_bytes, modified_ns, launch_target, ocr_text,
                            embedding, indexed_at
                        ) VALUES (?, 'app', ?, '.lnk', ?, ?, ?, ?, '', NULL, ?)
                        ON CONFLICT(path) DO UPDATE SET
                            item_type=excluded.item_type, name=excluded.name,
                            extension=excluded.extension, parent_path=excluded.parent_path,
                            size_bytes=excluded.size_bytes, modified_ns=excluded.modified_ns,
                            launch_target=excluded.launch_target, indexed_at=excluded.indexed_at
                        """,
                        (
                            key,
                            name,
                            parent_path,
                            size_bytes,
                            modified_ns,
                            launch_target,
                            time.time(),
                        ),
                    )
                rows = self.connection.execute(
                    "SELECT path FROM items WHERE item_type = 'app'"
                ).fetchall()
                stale = [
                    str(row["path"])
                    for row in rows
                    if any(
                        str(row["path"]) == root_key
                        or str(row["path"]).startswith(root_key + os.sep)
                        for root_key in enumerated_root_keys
                    )
                    and str(row["path"]) not in discovered
                ]
                if stale:
                    self.connection.executemany(
                        "DELETE FROM items WHERE item_type = 'app' AND path = ?",
                        ((path,) for path in stale),
                    )
                if is_cancelled():
                    raise RuntimeError("Application indexing was cancelled.")
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            count = len(discovered)
            return count

    def index_single_file(
        self,
        path: str | Path,
        *,
        ocr_extractor: Callable[[Path], str] | None = None,
        stability_check: bool = True,
    ) -> bool:
        """Index or re-index a single file. Returns True if indexed, False if skipped."""
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            candidate = Path(path).expanduser().resolve(strict=False)
            # Skip excluded directories
            for part in candidate.parts:
                if part.casefold() in {name.casefold() for name in DEFAULT_EXCLUDED_DIRECTORIES}:
                    return False
            try:
                stat = candidate.stat()
            except OSError:
                return False
            if not candidate.is_file():
                return False
            # File stability: if size/mtime changed between two reads, skip
            if stability_check:
                import time as _time
                _time.sleep(0.1)
                try:
                    stat2 = candidate.stat()
                except OSError:
                    return False
                if stat2.st_size != stat.st_size or stat2.st_mtime_ns != stat.st_mtime_ns:
                    return False  # File still changing
                stat = stat2
            key = self._key(candidate)
            extension = candidate.suffix.casefold()
            item_type = "screenshot" if extension in SUPPORTED_EXTENSIONS else "file"
            parent_key = self._key(candidate.parent)
            # Check existing record to reuse OCR
            previous = self.connection.execute(
                "SELECT size_bytes, modified_ns, ocr_text FROM items WHERE path = ?",
                (key,),
            ).fetchone()
            text = (
                str(previous["ocr_text"])
                if previous
                and previous["size_bytes"] == stat.st_size
                and previous["modified_ns"] == stat.st_mtime_ns
                else ""
            )
            if item_type == "screenshot" and not text and ocr_extractor:
                text = ocr_extractor(candidate)
            self.connection.execute(
                """
                INSERT INTO items(
                    path, item_type, name, extension, parent_path,
                    size_bytes, modified_ns, launch_target, ocr_text,
                    embedding, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?)
                ON CONFLICT(path) DO UPDATE SET
                    item_type=excluded.item_type, name=excluded.name,
                    extension=excluded.extension, parent_path=excluded.parent_path,
                    size_bytes=excluded.size_bytes, modified_ns=excluded.modified_ns,
                    ocr_text=excluded.ocr_text, indexed_at=excluded.indexed_at
                """,
                (key, item_type, candidate.name, extension, parent_key,
                 stat.st_size, stat.st_mtime_ns, text, time.time()),
            )
            self.connection.commit()
            return True

    def remove_item(self, path: str | Path) -> bool:
        """Remove a single item from the index. Returns True if a row was deleted."""
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            key = self._key(Path(path))
            self._next_pdf_generation_locked(key)
            cursor = self.connection.execute(
                "DELETE FROM items WHERE path = ?", (key,)
            )
            self.connection.commit()
            return cursor.rowcount > 0

    def remove_items_under(self, path: str | Path) -> int:
        """Remove all items at or under a path. Returns count of deleted rows."""
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            prefix = self._key(Path(path))
            active_pdf_paths = self.connection.execute(
                """
                SELECT path FROM pdf_documents
                WHERE path = ? OR path LIKE ?
                """,
                (prefix, prefix + os.sep + "%"),
            ).fetchall()
            for row in active_pdf_paths:
                self._next_pdf_generation_locked(str(row["path"]))
            for active_key in tuple(self._pdf_generations):
                if active_key == prefix or active_key.startswith(prefix + os.sep):
                    self._next_pdf_generation_locked(active_key)
            sep = os.sep
            cursor = self.connection.execute(
                "DELETE FROM items WHERE path = ? OR path LIKE ?",
                (prefix, prefix + sep + "%"),
            )
            self.connection.commit()
            return cursor.rowcount

    def index_single_directory(self, path: str | Path) -> bool:
        """Add or update a single directory entry. Returns True if inserted/updated."""
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            directory = Path(path).expanduser().resolve(strict=False)
            if not directory.is_dir():
                return False
            for part in directory.parts:
                if part.casefold() in {name.casefold() for name in DEFAULT_EXCLUDED_DIRECTORIES}:
                    return False
            try:
                stat = directory.stat()
            except OSError:
                return False
            key = self._key(directory)
            parent_key = self._key(directory.parent)
            self.connection.execute(
                """
                INSERT INTO items(
                    path, item_type, name, extension, parent_path,
                    size_bytes, modified_ns, launch_target, ocr_text,
                    embedding, indexed_at
                ) VALUES (?, 'folder', ?, '', ?, 0, ?, NULL, '', NULL, ?)
                ON CONFLICT(path) DO UPDATE SET
                    name=excluded.name, parent_path=excluded.parent_path,
                    modified_ns=excluded.modified_ns, indexed_at=excluded.indexed_at
                """,
                (key, directory.name, parent_key, stat.st_mtime_ns, time.time()),
            )
            self.connection.commit()
            return True

    # ------------------------------------------------------------------
    # Targeted single-item operations for incremental monitor updates
    # ------------------------------------------------------------------

    def index_single_file(
        self,
        path: str | Path,
        *,
        ocr_extractor: Callable[[Path], str] | None = None,
        stability_check: bool = True,
        stability_observations: int = 2,
        stability_interval: float = 0.1,
        wait: Callable[[float], None] = time.sleep,
        raise_on_deferred: bool = False,
    ) -> bool:
        """Index or re-index a single file without walking the whole folder.

        Preserves existing OCR text when ``size_bytes`` and ``modified_ns``
        both match the stored record (same contract as ``index_folder``).

        When ``stability_check`` is True, the file's metadata is read twice
        with a short pause between readings.  If the values differ, the file
        is still changing and the call returns False without writing anything.
        Pass ``stability_check=False`` in unit tests and when the caller has
        already confirmed the file is stable.

        Returns True if the record was written, False if skipped.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            candidate = Path(path).expanduser().resolve(strict=False)
            if candidate.suffix.casefold() == ".pdf":
                outcome = self.index_pdf(candidate)
                return outcome.action in {"indexed", "skipped", "status-only"}
            # Reject paths inside excluded directories
            excluded = {name.casefold() for name in DEFAULT_EXCLUDED_DIRECTORIES}
            if any(part.casefold() in excluded for part in candidate.parts):
                return False
            try:
                stat = candidate.stat()
            except OSError as error:
                if (
                    raise_on_deferred
                    and not isinstance(error, FileNotFoundError)
                    and _is_transient_file_error(error)
                ):
                    raise FileIntegrityDeferred(str(error)) from error
                return False
            if not candidate.is_file():
                return False
            # Optional stability check: re-read after a short pause
            if stability_check:
                required = max(1, stability_observations)
                stable = 0
                for _ in range(required):
                    wait(stability_interval)
                    try:
                        current = candidate.stat()
                    except OSError as error:
                        if (
                            raise_on_deferred
                            and not isinstance(error, FileNotFoundError)
                            and _is_transient_file_error(error)
                        ):
                            raise FileIntegrityDeferred(str(error)) from error
                        return False
                    if (
                        current.st_size == stat.st_size
                        and current.st_mtime_ns == stat.st_mtime_ns
                    ):
                        stable += 1
                        if stable >= required:
                            stat = current
                            break
                    else:
                        stable = 0
                    stat = current
                else:
                    if raise_on_deferred:
                        raise FileIntegrityDeferred(
                            f"file metadata kept changing: {candidate}"
                        )
                    return False
            key = self._key(candidate)
            extension = candidate.suffix.casefold()
            item_type = "screenshot" if extension in SUPPORTED_EXTENSIONS else "file"
            parent_key = self._key(candidate.parent)
            # Reuse existing OCR when metadata is unchanged
            previous = self.connection.execute(
                "SELECT size_bytes, modified_ns, ocr_text FROM items WHERE path = ?",
                (key,),
            ).fetchone()
            text = (
                str(previous["ocr_text"])
                if previous
                and previous["size_bytes"] == stat.st_size
                and previous["modified_ns"] == stat.st_mtime_ns
                else ""
            )
            if item_type == "screenshot" and not text and ocr_extractor:
                text = ocr_extractor(candidate)
            self.connection.execute(
                """
                INSERT INTO items(
                    path, item_type, name, extension, parent_path,
                    size_bytes, modified_ns, launch_target, ocr_text,
                    embedding, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?)
                ON CONFLICT(path) DO UPDATE SET
                    item_type=excluded.item_type, name=excluded.name,
                    extension=excluded.extension, parent_path=excluded.parent_path,
                    size_bytes=excluded.size_bytes, modified_ns=excluded.modified_ns,
                    ocr_text=excluded.ocr_text, indexed_at=excluded.indexed_at
                """,
                (
                    key,
                    item_type,
                    candidate.name,
                    extension,
                    parent_key,
                    stat.st_size,
                    stat.st_mtime_ns,
                    text,
                    time.time(),
                ),
            )
            self.connection.commit()
            return True

    def index_single_directory(self, path: str | Path) -> bool:
        """Add or update a single directory entry without recursing.

        Returns True if the record was written, False if skipped.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            directory = Path(path).expanduser().resolve(strict=False)
            excluded = {name.casefold() for name in DEFAULT_EXCLUDED_DIRECTORIES}
            if any(part.casefold() in excluded for part in directory.parts):
                return False
            if not directory.is_dir():
                return False
            try:
                stat = directory.stat()
            except OSError:
                return False
            key = self._key(directory)
            parent_key = self._key(directory.parent)
            self.connection.execute(
                """
                INSERT INTO items(
                    path, item_type, name, extension, parent_path,
                    size_bytes, modified_ns, launch_target, ocr_text,
                    embedding, indexed_at
                ) VALUES (?, 'folder', ?, '', ?, 0, ?, NULL, '', NULL, ?)
                ON CONFLICT(path) DO UPDATE SET
                    name=excluded.name, parent_path=excluded.parent_path,
                    modified_ns=excluded.modified_ns, indexed_at=excluded.indexed_at
                """,
                (key, directory.name, parent_key, stat.st_mtime_ns, time.time()),
            )
            self.connection.commit()
            return True

    def remove_item(self, path: str | Path) -> bool:
        """Remove a single item from the index by path.

        Returns True if a row was deleted, False if the path was not indexed.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            key = self._key(Path(path))
            self._next_pdf_generation_locked(key)
            cursor = self.connection.execute(
                "DELETE FROM items WHERE path = ?", (key,)
            )
            self._delete_pdf_records_locked((key,))
            self.connection.commit()
            return cursor.rowcount > 0

    def remove_items_under(self, path: str | Path) -> int:
        """Remove all items at or under a path (the item itself and descendants).

        Returns the count of deleted rows.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("LocalIndex is closed.")
            prefix = self._key(Path(path))
            active_pdf_paths = self.connection.execute(
                """
                SELECT path FROM pdf_documents
                WHERE path = ? OR path LIKE ?
                """,
                (prefix, prefix + os.sep + "%"),
            ).fetchall()
            for row in active_pdf_paths:
                self._next_pdf_generation_locked(str(row["path"]))
            for active_key in tuple(self._pdf_generations):
                if active_key == prefix or active_key.startswith(prefix + os.sep):
                    self._next_pdf_generation_locked(active_key)
            sep = os.sep
            cursor = self.connection.execute(
                "DELETE FROM items WHERE path = ? OR path LIKE ?",
                (prefix, prefix + sep + "%"),
            )
            self._delete_pdf_records_under_locked(prefix)
            self.connection.commit()
            return cursor.rowcount

    def search(self, query: str, category: str = "all", limit: int = 50) -> list[IndexedItem]:
        """Return candidates; ranking is performed by ``unified_search``."""
        with self._lock:
            if self._closed:
                return []
            normalized = query.strip()
            if not normalized:
                return []
            types = {"screenshots": ("screenshot",), "files": ("file",), "apps": ("app",)}
            selected = types.get(category, ("screenshot", "file", "app"))
            placeholders = ",".join("?" for _ in selected)
            rows = self.connection.execute(
                f"""
                SELECT item_type, name, path, extension, launch_target, ocr_text
                FROM items
                WHERE item_type IN ({placeholders})
                  AND (name LIKE ? COLLATE NOCASE OR path LIKE ? COLLATE NOCASE
                       OR ocr_text LIKE ? COLLATE NOCASE)
                LIMIT ?
                """,
                (*selected, f"%{normalized}%", f"%{normalized}%", f"%{normalized}%", limit * 4),
            ).fetchall()
            if not rows:
                rows = self.connection.execute(
                    f"""
                    SELECT item_type, name, path, extension, launch_target, ocr_text
                    FROM items WHERE item_type IN ({placeholders}) LIMIT ?
                    """,
                    (*selected, limit * 4),
                ).fetchall()
            return [
                IndexedItem(
                    item_type=row["item_type"],
                    name=row["name"],
                    path=Path(row["path"]),
                    extension=row["extension"],
                    launch_target=Path(row["launch_target"]) if row["launch_target"] else None,
                    ocr_text=row["ocr_text"],
                )
                for row in rows
            ]

    @staticmethod
    def _pdf_query_terms(query: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(re.findall(r"[^\W_]+", query.casefold(), re.UNICODE)))

    @staticmethod
    def _pdf_snippet(text: str, query: str, width: int = 260) -> str:
        compact = " ".join(text.split())
        if len(compact) <= width:
            return compact
        terms = LocalIndex._pdf_query_terms(query)
        lower = compact.casefold()
        positions = [lower.find(term) for term in terms if lower.find(term) >= 0]
        start = max(0, (min(positions) if positions else 0) - width // 3)
        end = min(len(compact), start + width)
        if start > 0:
            boundary = compact.find(" ", start)
            if boundary >= 0 and boundary < end:
                start = boundary + 1
        if end < len(compact):
            boundary = compact.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        return ("..." if start > 0 else "") + compact[start:end].strip() + (
            "..." if end < len(compact) else ""
        )

    def _pdf_fts_match_query(self, query: str) -> str:
        terms = self._pdf_query_terms(query)
        phrase = '"' + " ".join(terms).replace('"', '""') + '"'
        all_terms = " AND ".join(f"{term}*" for term in terms)
        return f"{phrase} OR ({all_terms})"

    def search_pdfs(
        self, query: str, limit: int = 30, *, pages_per_document: int = 3
    ) -> list[PDFSearchResult]:
        """Search persisted PDF page text without opening or extracting PDFs."""
        with self._lock:
            if self._closed or limit <= 0:
                return []
            normalized = query.strip()
            terms = self._pdf_query_terms(normalized)
            if not terms:
                return []
            candidate_limit = max(limit * pages_per_document * 8, 50)
            if self._pdf_fts_available:
                rows = self.connection.execute(
                    """
                    SELECT f.document_path, f.page_number, f.text,
                           d.title, d.extraction_status
                    FROM pdf_pages_fts AS f
                    JOIN pdf_documents AS d ON d.path = f.document_path
                    WHERE d.extraction_status = 'ok'
                      AND pdf_pages_fts MATCH ?
                    LIMIT ?
                    """,
                    (self._pdf_fts_match_query(normalized), candidate_limit),
                ).fetchall()
            else:
                clauses = " OR ".join("p.text LIKE ? COLLATE NOCASE" for _ in terms)
                rows = self.connection.execute(
                    f"""
                    SELECT p.document_path, p.page_number, p.text,
                           d.title, d.extraction_status
                    FROM pdf_pages AS p
                    JOIN pdf_documents AS d ON d.path = p.document_path
                    WHERE d.extraction_status = 'ok'
                      AND ({clauses})
                    LIMIT ?
                    """,
                    tuple(f"%{term}%" for term in terms) + (candidate_limit,),
                ).fetchall()

            ranked: list[tuple[float, sqlite3.Row, str]] = []
            phrase = " ".join(terms)
            for row in rows:
                text = str(row["text"])
                lowered = text.casefold()
                matched_terms = [term for term in terms if term in lowered]
                if not matched_terms:
                    continue
                frequency = sum(lowered.count(term) for term in matched_terms)
                exact_phrase = lowered.count(phrase)
                path = Path(str(row["document_path"]))
                name = path.name
                title = str(row["title"]) if row["title"] else None
                title_bonus = sum(
                    term in (name + " " + (title or "")).casefold()
                    for term in terms
                )
                score = (
                    exact_phrase * 1000
                    + (500 if len(matched_terms) == len(terms) else 0)
                    + len(matched_terms) * 50
                    + frequency
                    + title_bonus * 10
                )
                ranked.append((score, row, self._pdf_snippet(text, normalized)))

            ranked.sort(
                key=lambda value: (
                    -value[0],
                    str(value[1]["document_path"]).casefold(),
                    int(value[1]["page_number"]),
                )
            )
            per_document: dict[str, int] = {}
            results: list[PDFSearchResult] = []
            for score, row, snippet in ranked:
                path_text = str(row["document_path"])
                count = per_document.get(path_text, 0)
                if count >= pages_per_document:
                    continue
                per_document[path_text] = count + 1
                path = Path(path_text)
                results.append(
                    PDFSearchResult(
                        path=path,
                        name=path.name,
                        title=str(row["title"]) if row["title"] else None,
                        page_number=int(row["page_number"]),
                        snippet=snippet,
                        score=float(score),
                        extraction_status=str(row["extraction_status"]),
                        matched_terms=tuple(
                            term
                            for term in terms
                            if term in str(row["text"]).casefold()
                        ),
                    )
                )
                if len(results) >= limit:
                    break
            return results
