"""Tests for filesystem monitoring and targeted incremental indexing.

Design principles
-----------------
- All tests are deterministic: no test relies on the OS actually delivering
  watchdog events in real time.
- FolderMonitor tests inject ``ChangeEvent`` objects directly into the
  monitor's cross-thread queue and verify the debounce/emit logic.
- LocalIndex targeted-method tests use temporary directories and real files.
- build_watch_task tests verify correct event dispatch without starting a
  real watchdog observer.
- Desktop integration tests patch FolderMonitor to keep them headless.
"""

from __future__ import annotations

import os
import errno
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from snapseek.filesystem_monitor import ChangeEvent, DEBOUNCE_MS, FolderMonitor
from snapseek.unified_index import FileIntegrityDeferred, LocalIndex
from snapseek.ui.operations import build_reconcile_task, build_watch_task
from snapseek.ui.workers import WorkerOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pump(ms: int = DEBOUNCE_MS + 150) -> None:
    """Process Qt events for *ms* milliseconds."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# ChangeEvent unit tests
# ---------------------------------------------------------------------------

class ChangeEventTests(unittest.TestCase):
    def test_fields_file_created(self) -> None:
        e = ChangeEvent("created", "/tmp/foo.txt")
        self.assertEqual(e.kind, "created")
        self.assertEqual(e.src_path, "/tmp/foo.txt")
        self.assertIsNone(e.dest_path)
        self.assertFalse(e.is_directory)

    def test_fields_move(self) -> None:
        e = ChangeEvent("moved", "/tmp/a.txt", "/tmp/b.txt", is_directory=False)
        self.assertEqual(e.dest_path, "/tmp/b.txt")

    def test_directory_flag(self) -> None:
        e = ChangeEvent("deleted", "/tmp/d", is_directory=True)
        self.assertTrue(e.is_directory)

    def test_equality_and_hash(self) -> None:
        a = ChangeEvent("created", "/x/y.txt")
        b = ChangeEvent("created", "/x/y.txt")
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        self.assertIn(a, {b})

    def test_inequality(self) -> None:
        a = ChangeEvent("created", "/x/y.txt")
        b = ChangeEvent("modified", "/x/y.txt")
        self.assertNotEqual(a, b)

    def test_repr(self) -> None:
        e = ChangeEvent("moved", "/a", "/b")
        self.assertIn("moved", repr(e))
        self.assertIn("/b", repr(e))


# ---------------------------------------------------------------------------
# LocalIndex targeted-method tests
# ---------------------------------------------------------------------------

class LocalIndexSingleFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.index = LocalIndex(self.root / "index.sqlite3")

    def tearDown(self) -> None:
        self.index.close()
        self.tmp.cleanup()

    # --- index_single_file ---

    def test_adds_new_file(self) -> None:
        f = self.root / "hello.txt"
        f.write_text("hello world", encoding="utf-8")
        result = self.index.index_single_file(f, stability_check=False)
        self.assertTrue(result)
        items = self.index.search("hello", "files")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "hello.txt")

    def test_updates_existing_file(self) -> None:
        f = self.root / "update.txt"
        f.write_text("original", encoding="utf-8")
        self.index.index_single_file(f, stability_check=False)
        # Force a different mtime by re-writing
        time.sleep(0.01)
        f.write_text("updated content", encoding="utf-8")
        self.index.index_single_file(f, stability_check=False)
        items = self.index.search("update", "files")
        self.assertEqual(len(items), 1)  # no duplicates

    def test_skips_nonexistent_path(self) -> None:
        result = self.index.index_single_file(
            self.root / "ghost.txt", stability_check=False
        )
        self.assertFalse(result)

    def test_skips_directory_path(self) -> None:
        subdir = self.root / "adir"
        subdir.mkdir()
        result = self.index.index_single_file(subdir, stability_check=False)
        self.assertFalse(result)

    def test_skips_path_inside_excluded_directory(self) -> None:
        git = self.root / ".git"
        git.mkdir()
        cfg = git / "config"
        cfg.write_text("git config", encoding="utf-8")
        result = self.index.index_single_file(cfg, stability_check=False)
        self.assertFalse(result)
        self.assertEqual(self.index.search("config", "files"), [])

    def test_preserves_ocr_when_metadata_unchanged(self) -> None:
        f = self.root / "screen.png"
        f.write_bytes(b"fake png data")
        calls: list[Path] = []
        extractor = lambda p: calls.append(p) or "invoice total"  # noqa: E731
        self.index.index_single_file(f, ocr_extractor=extractor, stability_check=False)
        self.assertEqual(calls, [f])
        # Second call — same size/mtime — must NOT re-run OCR
        self.index.index_single_file(f, ocr_extractor=extractor, stability_check=False)
        self.assertEqual(calls, [f])  # still only 1 OCR call
        items = self.index.search("invoice", "screenshots")
        self.assertEqual(items[0].ocr_text, "invoice total")

    def test_reruns_ocr_when_file_modified(self) -> None:
        f = self.root / "changed.png"
        f.write_bytes(b"v1")
        calls: list[str] = []
        self.index.index_single_file(
            f,
            ocr_extractor=lambda p: calls.append("v1") or "first",
            stability_check=False,
        )
        time.sleep(0.01)
        f.write_bytes(b"v2_longer")
        self.index.index_single_file(
            f,
            ocr_extractor=lambda p: calls.append("v2") or "second",
            stability_check=False,
        )
        self.assertEqual(calls, ["v1", "v2"])
        items = self.index.search("second", "screenshots")
        self.assertEqual(len(items), 1)

    def test_no_duplicate_records_on_repeated_calls(self) -> None:
        f = self.root / "dup.txt"
        f.write_text("duplicate test", encoding="utf-8")
        for _ in range(5):
            self.index.index_single_file(f, stability_check=False)
        items = self.index.search("dup", "files")
        self.assertEqual(len(items), 1)

    def test_screenshot_classified_by_extension(self) -> None:
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            f = self.root / f"img{ext}"
            f.write_bytes(b"img")
            self.index.index_single_file(f, stability_check=False)
            items = self.index.search(f"img{ext}", "screenshots")
            self.assertTrue(
                any(i.item_type == "screenshot" for i in items),
                f"{ext} should be classified as screenshot",
            )

    def test_non_image_classified_as_file(self) -> None:
        f = self.root / "document.pdf"
        f.write_bytes(b"pdf content")
        self.index.index_single_file(f, stability_check=False)
        items = self.index.search("document", "files")
        self.assertEqual(items[0].item_type, "file")

    def test_stability_check_skips_unstable_file(self) -> None:
        """A file whose metadata changes between two reads is skipped."""
        f = self.root / "unstable.txt"
        f.write_text("v1", encoding="utf-8")
        stat1 = f.stat()

        call_count = [0]

        def fake_stat(path: Path) -> object:
            call_count[0] += 1
            if call_count[0] == 1:
                return stat1
            # Return a stat-like object with different mtime
            class FakeStat:
                st_size = stat1.st_size
                st_mtime_ns = stat1.st_mtime_ns + 1_000_000
            return FakeStat()

        with patch.object(Path, "stat", fake_stat), \
             patch.object(Path, "is_file", return_value=True):
            result = self.index.index_single_file(f, stability_check=True)
        self.assertFalse(result)

    def test_stability_check_requires_consecutive_observations(self) -> None:
        f = self.root / "stable.txt"
        f.write_text("stable", encoding="utf-8")
        waits: list[float] = []
        result = self.index.index_single_file(
            f,
            stability_check=True,
            stability_interval=0.25,
            wait=waits.append,
        )
        self.assertTrue(result)
        self.assertEqual(waits, [0.25, 0.25])

    def test_disappearing_file_is_not_indexed(self) -> None:
        f = self.root / "vanished.txt"
        f.write_text("vanish", encoding="utf-8")
        original_stat = Path.stat
        calls = [0]

        def stat_then_disappear(path: Path, **_kwargs):
            calls[0] += 1
            if calls[0] == 2:
                raise FileNotFoundError(str(path))
            return original_stat(path)

        with patch.object(Path, "stat", stat_then_disappear), patch.object(
            Path, "is_file", return_value=True
        ):
            result = self.index.index_single_file(
                f, stability_check=True, wait=lambda _delay: None
            )
        self.assertFalse(result)
        self.assertEqual(self.index.search("vanish", "files"), [])

    def test_raises_when_closed(self) -> None:
        f = self.root / "x.txt"
        f.write_text("x", encoding="utf-8")
        self.index.close()
        with self.assertRaises(RuntimeError):
            self.index.index_single_file(f)

    def test_case_insensitive_key_deduplication(self) -> None:
        """Two paths differing only by case must produce the same key."""
        f = self.root / "Mixed.TXT"
        f.write_text("case test", encoding="utf-8")
        key_lower = LocalIndex._key(self.root / "mixed.txt")
        key_upper = LocalIndex._key(self.root / "MIXED.TXT")
        key_orig  = LocalIndex._key(f)
        # All three keys should be identical (casefold)
        self.assertEqual(key_lower, key_upper)
        self.assertEqual(key_lower, key_orig)

    # --- index_single_directory ---

    def test_adds_directory_entry(self) -> None:
        subdir = self.root / "photos"
        subdir.mkdir()
        result = self.index.index_single_directory(subdir)
        self.assertTrue(result)
        # Folders are not returned by the search API (which covers files/apps/screenshots).
        # Verify directly via the raw connection.
        key = LocalIndex._key(subdir)
        row = self.index.connection.execute(
            "SELECT item_type FROM items WHERE path = ?", (key,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["item_type"], "folder")

    def test_skips_nonexistent_directory(self) -> None:
        result = self.index.index_single_directory(self.root / "missing")
        self.assertFalse(result)

    def test_skips_excluded_directory(self) -> None:
        excluded = self.root / "__pycache__"
        excluded.mkdir()
        result = self.index.index_single_directory(excluded)
        self.assertFalse(result)

    def test_raises_when_closed_directory(self) -> None:
        subdir = self.root / "sub"
        subdir.mkdir()
        self.index.close()
        with self.assertRaises(RuntimeError):
            self.index.index_single_directory(subdir)

    # --- remove_item ---

    def test_removes_indexed_file(self) -> None:
        f = self.root / "bye.txt"
        f.write_text("bye", encoding="utf-8")
        self.index.index_single_file(f, stability_check=False)
        removed = self.index.remove_item(f)
        self.assertTrue(removed)
        self.assertEqual(self.index.search("bye", "files"), [])

    def test_returns_false_for_unindexed_path(self) -> None:
        result = self.index.remove_item(self.root / "phantom.txt")
        self.assertFalse(result)

    def test_raises_when_closed_remove(self) -> None:
        self.index.close()
        with self.assertRaises(RuntimeError):
            self.index.remove_item(self.root / "x.txt")

    # --- remove_items_under ---

    def test_removes_all_descendants(self) -> None:
        subdir = self.root / "subdir"
        subdir.mkdir()
        files = []
        for name in ("a.txt", "b.txt", "c.txt"):
            p = subdir / name
            p.write_text(name, encoding="utf-8")
            self.index.index_single_file(p, stability_check=False)
            files.append(p)
        count = self.index.remove_items_under(subdir)
        self.assertEqual(count, 3)
        for p in files:
            self.assertEqual(self.index.search(p.stem, "files"), [])

    def test_removes_item_itself(self) -> None:
        f = self.root / "lone.txt"
        f.write_text("lone", encoding="utf-8")
        self.index.index_single_file(f, stability_check=False)
        count = self.index.remove_items_under(f)
        self.assertEqual(count, 1)
        self.assertEqual(self.index.search("lone", "files"), [])

    def test_safe_for_unindexed_path(self) -> None:
        count = self.index.remove_items_under(self.root / "never_existed")
        self.assertEqual(count, 0)

    def test_raises_when_closed_remove_under(self) -> None:
        self.index.close()
        with self.assertRaises(RuntimeError):
            self.index.remove_items_under(self.root)

    def test_does_not_remove_sibling_directories(self) -> None:
        a = self.root / "aa"
        b = self.root / "ab"
        a.mkdir()
        b.mkdir()
        fa = a / "file_a.txt"
        fb = b / "file_b.txt"
        fa.write_text("a", encoding="utf-8")
        fb.write_text("b", encoding="utf-8")
        self.index.index_single_file(fa, stability_check=False)
        self.index.index_single_file(fb, stability_check=False)
        self.index.remove_items_under(a)
        # sibling directory "ab" contents must still be present
        self.assertEqual(len(self.index.search("file_b", "files")), 1)


# ---------------------------------------------------------------------------
# FolderMonitor deterministic tests (no real OS events)
# ---------------------------------------------------------------------------

class FolderMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        self.monitor = FolderMonitor()
        self.received: list[frozenset] = []
        self.monitor.changes_ready.connect(self.received.append)

    def tearDown(self) -> None:
        self.monitor.stop()
        self.monitor.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def _inject(
        self,
        kind: str,
        src: str,
        dest: str | None = None,
        *,
        is_dir: bool = False,
    ) -> None:
        """Put a ChangeEvent into the monitor's cross-thread queue."""
        self.monitor._event_queue.put(
            ChangeEvent(kind, src, dest, is_directory=is_dir)
        )

    def _start_polling(self) -> None:
        self.monitor._poll_timer.start()

    # --- basic event delivery ---

    def test_single_create_event_emits_after_debounce(self) -> None:
        self._start_polling()
        self._inject("created", "/tmp/test.txt")
        _pump(DEBOUNCE_MS + 200)
        self.assertEqual(len(self.received), 1)
        events = list(self.received[0])
        self.assertEqual(events[0].kind, "created")
        self.assertEqual(events[0].src_path, "/tmp/test.txt")

    def test_delete_event_delivered(self) -> None:
        self._start_polling()
        self._inject("deleted", "/tmp/gone.txt")
        _pump(DEBOUNCE_MS + 200)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(list(self.received[0])[0].kind, "deleted")

    def test_moved_event_carries_dest_path(self) -> None:
        self._start_polling()
        self._inject("moved", "/tmp/old.txt", "/tmp/new.txt")
        _pump(DEBOUNCE_MS + 200)
        events = list(self.received[0])
        self.assertEqual(events[0].kind, "moved")
        self.assertEqual(events[0].dest_path, "/tmp/new.txt")

    def test_directory_event_has_is_directory_flag(self) -> None:
        self._start_polling()
        self._inject("created", "/tmp/newdir", is_dir=True)
        _pump(DEBOUNCE_MS + 200)
        self.assertTrue(list(self.received[0])[0].is_directory)

    # --- batching and deduplication ---

    def test_burst_of_events_emitted_as_one_batch(self) -> None:
        self._start_polling()
        for i in range(30):
            self._inject("modified", f"/tmp/file{i}.txt")
        _pump(DEBOUNCE_MS + 200)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(len(self.received[0]), 30)

    def test_identical_events_deduplicated_via_frozenset(self) -> None:
        """Duplicate ChangeEvent objects (same kind/src/dest/is_dir) appear
        only once in the emitted frozenset."""
        self._start_polling()
        event = ChangeEvent("modified", "/tmp/same.txt")
        for _ in range(10):
            self.monitor._event_queue.put(event)
        _pump(DEBOUNCE_MS + 200)
        # frozenset deduplicates equal events
        self.assertEqual(len(self.received[0]), 1)

    def test_debounce_resets_on_each_event(self) -> None:
        """Events arriving in two bursts separated by less than DEBOUNCE_MS
        are coalesced into a single emission."""
        self._start_polling()
        self._inject("created", "/tmp/a.txt")
        # Wait less than the debounce period, then inject again
        _pump(DEBOUNCE_MS // 2)
        self._inject("created", "/tmp/b.txt")
        _pump(DEBOUNCE_MS + 200)
        # Should be exactly one emission containing both events
        self.assertEqual(len(self.received), 1)
        paths = {e.src_path for e in self.received[0]}
        self.assertIn("/tmp/a.txt", paths)
        self.assertIn("/tmp/b.txt", paths)

    # --- excluded directories ---

    def test_excluded_src_path_is_dropped(self) -> None:
        self._start_polling()
        self._inject("created", "/project/.git/COMMIT_EDITMSG")
        _pump(DEBOUNCE_MS + 200)
        self.assertEqual(len(self.received), 0)

    def test_excluded_dest_path_is_dropped(self) -> None:
        self._start_polling()
        self._inject("moved", "/project/src.py", "/project/.venv/lib/x.py")
        _pump(DEBOUNCE_MS + 200)
        self.assertEqual(len(self.received), 0)

    def test_non_excluded_path_passes_through(self) -> None:
        self._start_polling()
        self._inject("created", "/project/src/main.py")
        _pump(DEBOUNCE_MS + 200)
        self.assertEqual(len(self.received), 1)

    def test_all_excluded_dirs_filtered(self) -> None:
        excluded_dirs = [
            ".git", ".venv", "__pycache__", "build", "dist",
            "node_modules", ".cache",
        ]
        self._start_polling()
        for d in excluded_dirs:
            self._inject("created", f"/project/{d}/something.txt")
        _pump(DEBOUNCE_MS + 200)
        self.assertEqual(len(self.received), 0)

    # --- lifecycle ---

    def test_stop_cancels_pending_debounce(self) -> None:
        self._start_polling()
        self._inject("created", "/tmp/test.txt")
        self.monitor.stop()
        _pump(DEBOUNCE_MS + 200)
        self.assertEqual(len(self.received), 0)

    def test_stop_clears_pending_list(self) -> None:
        self._start_polling()
        self._inject("created", "/tmp/test.txt")
        # Drain queue manually so _pending gets populated
        self.monitor._drain_queue()
        self.assertGreater(len(self.monitor._pending), 0)
        self.monitor.stop()
        self.assertEqual(len(self.monitor._pending), 0)

    def test_is_active_false_before_start(self) -> None:
        self.assertFalse(self.monitor.is_active())

    def test_stop_is_idempotent(self) -> None:
        self.monitor.stop()
        self.monitor.stop()  # must not raise

    def test_start_unavailable_directory_returns_false(self) -> None:
        result = self.monitor.start(Path("/path/that/does/not/exist"))
        self.assertFalse(result)
        self.assertFalse(self.monitor.is_active())

    def test_monitor_error_signal_emitted_on_start_failure(self) -> None:
        errors: list[str] = []
        self.monitor.monitor_error.connect(errors.append)
        with patch(
            "snapseek.filesystem_monitor.Observer",
            side_effect=OSError("permission denied"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                self.monitor.start(Path(tmp))
        self.assertTrue(len(errors) > 0)

    def test_start_roots_monitors_available_roots_and_retains_missing_root(self) -> None:
        class FakeObserver:
            instances: list["FakeObserver"] = []

            def __init__(self) -> None:
                self.scheduled: list[str] = []
                self.started = False
                self.stopped = False
                self.__class__.instances.append(self)

            def schedule(self, _handler, path: str, recursive: bool) -> None:
                self.scheduled.append(path)

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.stopped = True

            def join(self, timeout: float) -> None:
                pass

            def is_alive(self) -> bool:
                return self.started and not self.stopped

        with tempfile.TemporaryDirectory() as tmp:
            available = Path(tmp) / "available"
            missing = Path(tmp) / "missing"
            available.mkdir()
            with patch("snapseek.filesystem_monitor.Observer", FakeObserver):
                self.assertTrue(self.monitor.start_roots((available, missing)))
                self.assertEqual(self.monitor.configured_roots(), (available, missing))
                self.assertEqual(self.monitor._active_roots, {available})
                missing.mkdir()
                self.assertTrue(self.monitor.recover())
                self.assertEqual(self.monitor._active_roots, {available, missing})
                self.assertEqual(len(FakeObserver.instances), 1)
                self.assertEqual(
                    FakeObserver.instances[0].scheduled,
                    [str(available), str(missing)],
                )

    def test_unexpected_observer_termination_is_reported(self) -> None:
        class DeadObserver:
            def is_alive(self) -> bool:
                return False

            def stop(self) -> None:
                pass

            def join(self, timeout: float) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            self.monitor._root = Path(tmp)
            self.monitor._observer = DeadObserver()
            states: list[str] = []
            self.monitor.monitor_health.connect(states.append)
            self.monitor._health_timer.start()
            self.monitor._check_health()
            self.assertEqual(states, ["failed"])
            self.assertEqual(self.monitor._root, Path(tmp))
            self.assertFalse(self.monitor.is_active())

    def test_missing_root_is_reported_and_retained_for_recovery(self) -> None:
        class LiveObserver:
            def is_alive(self) -> bool:
                return True

            def stop(self) -> None:
                pass

            def join(self, timeout: float) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.monitor._root = root
            self.monitor._observer = LiveObserver()
            states: list[str] = []
            self.monitor.monitor_health.connect(states.append)
            root.rmdir()
            self.monitor._check_health()
            self.assertEqual(states, ["root_missing"])
            self.assertEqual(self.monitor._root, root)

    def test_no_emission_without_poll_timer_running(self) -> None:
        """Events added to the queue but without the poll timer running
        must not trigger any emission."""
        # Do NOT call _start_polling()
        self._inject("created", "/tmp/silent.txt")
        _pump(DEBOUNCE_MS + 200)
        self.assertEqual(len(self.received), 0)


# ---------------------------------------------------------------------------
# build_watch_task correctness tests
# ---------------------------------------------------------------------------

class BuildWatchTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.index = LocalIndex(self.root / "idx.sqlite3")

    def tearDown(self) -> None:
        self.index.close()
        self.tmp.cleanup()

    def _run(self, events: list[ChangeEvent]) -> object:
        cancel = threading.Event()
        task = build_watch_task(
            self.root, index=self.index, pending_events=frozenset(events)
        )
        return task(cancel)

    # --- create ---

    def test_create_event_indexes_file(self) -> None:
        f = self.root / "new.txt"
        f.write_text("hello", encoding="utf-8")
        self._run([ChangeEvent("created", str(f))])
        self.assertEqual(len(self.index.search("new", "files")), 1)


    def test_create_event_indexes_directory_recursively(self) -> None:
        subdir = self.root / "newdir"
        subdir.mkdir()
        (subdir / "child.txt").write_text("child content", encoding="utf-8")
        self._run([ChangeEvent("created", str(subdir), is_directory=True)])
        items = self.index.search("child", "files")
        self.assertEqual(len(items), 1)

    def test_create_event_safe_for_vanished_file(self) -> None:
        """If a file is created then immediately deleted, the task must not raise."""
        path = str(self.root / "ephemeral.txt")
        # File does not exist — index_single_file should return False silently
        result = self._run([ChangeEvent("created", path)])
        self.assertIsNotNone(result)

    def test_transient_integrity_error_succeeds_on_retry(self) -> None:
        f = self.root / "locked.txt"
        f.write_text("locked", encoding="utf-8")
        with patch.object(
            self.index,
            "index_single_file",
            side_effect=[FileIntegrityDeferred("locked"), True],
        ) as index_file:
            result = build_watch_task(
                self.root,
                index=self.index,
                pending_events=frozenset([ChangeEvent("created", str(f))]),
                retry_delays=(0.5,),
                wait=lambda _delay: None,
            )(threading.Event())
        self.assertEqual(result, 1)
        self.assertEqual(index_file.call_count, 2)

    def test_retry_exhaustion_defers_file_without_deleting_existing_record(self) -> None:
        f = self.root / "existing.txt"
        f.write_text("existing", encoding="utf-8")
        self.index.index_single_file(f, stability_check=False)
        with patch.object(
            self.index,
            "index_single_file",
            side_effect=FileIntegrityDeferred("temporarily locked"),
        ) as index_file:
            result = build_watch_task(
                self.root,
                index=self.index,
                pending_events=frozenset([ChangeEvent("modified", str(f))]),
                retry_delays=(0.0, 0.0),
                wait=lambda _delay: None,
            )(threading.Event())
        self.assertEqual(result, 0)
        self.assertEqual(index_file.call_count, 3)
        self.assertEqual(len(self.index.search("existing", "files")), 1)

    def test_cancellation_stops_retry(self) -> None:
        f = self.root / "cancel.txt"
        f.write_text("cancel", encoding="utf-8")
        cancel = threading.Event()

        def fail_and_cancel(*_args, **_kwargs):
            cancel.set()
            raise FileIntegrityDeferred("locked")

        with patch.object(self.index, "index_single_file", side_effect=fail_and_cancel) as index_file:
            result = build_watch_task(
                self.root,
                index=self.index,
                pending_events=frozenset([ChangeEvent("created", str(f))]),
                retry_delays=(1.0, 1.0),
                wait=lambda _delay: None,
            )(cancel)
        self.assertEqual(result, 0)
        self.assertEqual(index_file.call_count, 1)

    def test_permanent_error_is_not_retried(self) -> None:
        f = self.root / "permanent.txt"
        f.write_text("permanent", encoding="utf-8")
        with patch.object(
            self.index,
            "index_single_file",
            side_effect=ValueError("invalid metadata"),
        ) as index_file:
            result = build_watch_task(
                self.root,
                index=self.index,
                pending_events=frozenset([ChangeEvent("created", str(f))]),
                retry_delays=(0.0, 0.0),
                wait=lambda _delay: None,
            )(threading.Event())
        self.assertEqual(result, 0)
        self.assertEqual(index_file.call_count, 1)

    def test_permanent_filesystem_error_is_not_retried(self) -> None:
        f = self.root / "invalid.txt"
        f.write_text("invalid", encoding="utf-8")
        error = OSError(errno.EINVAL, "invalid argument")
        with patch.object(
            self.index,
            "index_single_file",
            side_effect=error,
        ) as index_file:
            result = build_watch_task(
                self.root,
                index=self.index,
                pending_events=frozenset([ChangeEvent("created", str(f))]),
                retry_delays=(0.0, 0.0),
                wait=lambda _delay: None,
            )(threading.Event())
        self.assertEqual(result, 0)
        self.assertEqual(index_file.call_count, 1)

    def test_rename_indexes_stable_destination_before_removing_source(self) -> None:
        src = self.root / "before.txt"
        dest = self.root / "after.txt"
        src.write_text("renamed", encoding="utf-8")
        self.index.index_single_file(src, stability_check=False)
        src.rename(dest)
        result = build_watch_task(
            self.root,
            index=self.index,
            pending_events=frozenset([ChangeEvent("moved", str(src), str(dest))]),
            retry_delays=(),
            wait=lambda _delay: None,
        )(threading.Event())
        self.assertEqual(result, 1)
        self.assertEqual(len(self.index.search("renamed", "files")), 1)
        self.assertIsNone(
            self.index.connection.execute(
                "SELECT path FROM items WHERE path = ?",
                (LocalIndex._key(src),),
            ).fetchone()
        )

    # --- delete ---

    def test_delete_event_removes_file_record(self) -> None:
        f = self.root / "gone.txt"
        f.write_text("bye", encoding="utf-8")
        self.index.index_single_file(f, stability_check=False)
        f.unlink()
        self._run([ChangeEvent("deleted", str(f))])
        self.assertEqual(self.index.search("gone", "files"), [])

    def test_delete_directory_removes_all_descendants(self) -> None:
        subdir = self.root / "rmdir"
        subdir.mkdir()
        for name in ("x.txt", "y.txt"):
            p = subdir / name
            p.write_text(name, encoding="utf-8")
            self.index.index_single_file(p, stability_check=False)
        self._run([ChangeEvent("deleted", str(subdir), is_directory=True)])
        self.assertEqual(self.index.search("x.txt", "files"), [])
        self.assertEqual(self.index.search("y.txt", "files"), [])

    def test_delete_unindexed_path_is_safe(self) -> None:
        """Deleting a path that was never indexed must not raise."""
        result = self._run([ChangeEvent("deleted", str(self.root / "phantom.txt"))])
        self.assertIsNotNone(result)

    # --- modify ---

    def test_modify_event_updates_record(self) -> None:
        f = self.root / "edit.txt"
        f.write_text("original", encoding="utf-8")
        self.index.index_single_file(f, stability_check=False)
        time.sleep(0.01)
        f.write_text("modified content", encoding="utf-8")
        self._run([ChangeEvent("modified", str(f))])
        items = self.index.search("edit", "files")
        self.assertEqual(len(items), 1)  # no duplicates

    def test_unchanged_file_modify_event_does_not_rerun_ocr(self) -> None:
        """A 'modified' event for a file whose metadata hasn't changed must
        reuse the cached OCR text and not call the extractor again."""
        f = self.root / "stable.png"
        f.write_bytes(b"png_data")
        calls: list[Path] = []
        # Index with an extractor
        self.index.index_single_file(
            f,
            ocr_extractor=lambda p: calls.append(p) or "original ocr",
            stability_check=False,
        )
        self.assertEqual(len(calls), 1)
        # Now simulate a modify event WITHOUT passing an ocr_extractor through
        # build_watch_task (which doesn't pass one by default).  The OCR text
        # in the DB must still be "original ocr" because metadata unchanged.
        self._run([ChangeEvent("modified", str(f))])
        items = self.index.search("original", "screenshots")
        self.assertEqual(items[0].ocr_text, "original ocr")
        # Extractor was called only once (during the first index_single_file)
        self.assertEqual(len(calls), 1)

    # --- move / rename ---

    def test_move_removes_old_and_indexes_new(self) -> None:
        src = self.root / "before_move.txt"
        dest = self.root / "after_rename.txt"
        src.write_text("moved content", encoding="utf-8")
        self.index.index_single_file(src, stability_check=False)
        src.rename(dest)
        self._run([ChangeEvent("moved", str(src), str(dest))])
        # Old record must be gone — check by key
        old_key = LocalIndex._key(src)
        row = self.index.connection.execute(
            "SELECT path FROM items WHERE path = ?", (old_key,)
        ).fetchone()
        self.assertIsNone(row)
        # New record must exist
        new_key = LocalIndex._key(dest)
        row = self.index.connection.execute(
            "SELECT path FROM items WHERE path = ?", (new_key,)
        ).fetchone()
        self.assertIsNotNone(row)

    def test_move_directory_removes_old_and_indexes_new_tree(self) -> None:
        src_dir = self.root / "srcdir_abc"
        src_dir.mkdir()
        f = src_dir / "deep_content.txt"
        f.write_text("deep", encoding="utf-8")
        self.index.index_single_file(f, stability_check=False)

        dest_dir = self.root / "destdir_xyz"
        src_dir.rename(dest_dir)
        self._run([ChangeEvent("moved", str(src_dir), str(dest_dir), is_directory=True)])

        # All records under the old path must be gone
        old_key_prefix = LocalIndex._key(src_dir)
        rows = self.index.connection.execute(
            "SELECT path FROM items WHERE path = ? OR path LIKE ?",
            (old_key_prefix, old_key_prefix + os.sep + "%"),
        ).fetchall()
        self.assertEqual(rows, [])

        # New directory tree must be indexed
        new_file = dest_dir / "deep_content.txt"
        new_key = LocalIndex._key(new_file)
        row = self.index.connection.execute(
            "SELECT path FROM items WHERE path = ?", (new_key,)
        ).fetchone()
        self.assertIsNotNone(row)

    # --- efficiency ---

    def test_single_file_modify_does_not_rescan_root(self) -> None:
        """A targeted modify event must NOT call index_folder (full rescan)."""
        f = self.root / "single.txt"
        f.write_text("single", encoding="utf-8")
        with patch.object(self.index, "index_folder", wraps=self.index.index_folder) as mock_full:
            self._run([ChangeEvent("modified", str(f))])
        mock_full.assert_not_called()

    def test_burst_of_events_processed_without_duplicate_records(self) -> None:
        """Sending 10 create events for the same file must produce one record."""
        f = self.root / "repeated.txt"
        f.write_text("once", encoding="utf-8")
        events = [ChangeEvent("created", str(f)) for _ in range(10)]
        self._run(events)
        items = self.index.search("repeated", "files")
        self.assertEqual(len(items), 1)

    # --- cancellation ---

    def test_pre_cancelled_task_returns_none(self) -> None:
        f = self.root / "cancel.txt"
        f.write_text("x", encoding="utf-8")
        cancel = threading.Event()
        cancel.set()
        task = build_watch_task(
            self.root,
            index=self.index,
            pending_events=frozenset([ChangeEvent("created", str(f))]),
        )
        result = task(cancel)
        self.assertIsNone(result)

    def test_unsupported_file_type_indexed_as_file(self) -> None:
        f = self.root / "report.docx"
        f.write_bytes(b"docx content")
        self._run([ChangeEvent("created", str(f))])
        items = self.index.search("report", "files")
        self.assertEqual(items[0].item_type, "file")

    def test_multiple_different_events_processed_in_one_task(self) -> None:
        created = self.root / "created_qwerty.txt"
        to_delete = self.root / "to_delete_qwerty.txt"
        created.write_text("created content", encoding="utf-8")
        to_delete.write_text("will be removed", encoding="utf-8")
        self.index.index_single_file(to_delete, stability_check=False)
        to_delete.unlink()
        events = [
            ChangeEvent("created", str(created)),
            ChangeEvent("deleted", str(to_delete)),
        ]
        self._run(events)
        # created file must now be in the index
        created_key = LocalIndex._key(created)
        row = self.index.connection.execute(
            "SELECT path FROM items WHERE path = ?", (created_key,)
        ).fetchone()
        self.assertIsNotNone(row)
        # deleted file must be gone
        deleted_key = LocalIndex._key(to_delete)
        row = self.index.connection.execute(
            "SELECT path FROM items WHERE path = ?", (deleted_key,)
        ).fetchone()
        self.assertIsNone(row)


class ReconciliationTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.index = LocalIndex(self.root / "idx.sqlite3")

    def tearDown(self) -> None:
        self.index.close()
        self.tmp.cleanup()

    def test_reconciliation_recovers_missed_create_and_delete(self) -> None:
        existing = self.root / "existing.txt"
        existing.write_text("existing", encoding="utf-8")
        self.index.index_folder(self.root)
        existing.unlink()
        created = self.root / "created.txt"
        created.write_text("created", encoding="utf-8")
        result = build_reconcile_task(self.root, index=self.index)(threading.Event())
        self.assertGreaterEqual(result, 1)
        self.assertEqual(self.index.search("created", "files")[0].name, "created.txt")
        row = self.index.connection.execute(
            "SELECT path FROM items WHERE path = ?",
            (LocalIndex._key(existing),),
        ).fetchone()
        self.assertIsNone(row)

    def test_reconciliation_reuses_unchanged_ocr(self) -> None:
        image = self.root / "capture.png"
        image.write_bytes(b"png")
        calls: list[Path] = []
        self.index.index_folder(
            self.root,
            ocr_extractor=lambda path: calls.append(path) or "captured",
        )
        self.index.index_folder(
            self.root,
            ocr_extractor=lambda path: calls.append(path) or "changed",
        )
        self.assertEqual(calls, [image])
        row = self.index.connection.execute(
            "SELECT ocr_text FROM items WHERE path = ?",
            (LocalIndex._key(image),),
        ).fetchone()
        self.assertEqual(row["ocr_text"], "captured")


# ---------------------------------------------------------------------------
# Desktop integration — monitor lifecycle
# ---------------------------------------------------------------------------

class DesktopMonitorIntegrationTests(unittest.TestCase):
    """Headless tests for monitor start/stop and pending-event dispatch.

    MainWindow is constructed normally (with a real FolderMonitor) and then
    the monitor attribute is replaced with a MagicMock so no real watchdog
    observer is started.  This avoids the complexities of patching a class
    that is imported at module load time.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()

    def setUp(self) -> None:
        from snapseek.desktop import MainWindow
        self.window = MainWindow()
        # Replace the real FolderMonitor with a mock *after* construction so
        # signal connections already made in __init__ still work.  We keep the
        # real signals from the original monitor for tests that need them.
        self._real_monitor = self.window._monitor
        mock = MagicMock(spec=FolderMonitor)
        mock.is_active.return_value = False
        mock._root = None
        # Forward the real signals so signal connections still work
        mock.changes_ready = self._real_monitor.changes_ready
        mock.monitor_error  = self._real_monitor.monitor_error
        self.window._monitor = mock

    def tearDown(self) -> None:
        # Stop the real monitor (poll timer, etc.) cleanly
        self._real_monitor.stop()
        # Force final close without calling mock.stop() which might interfere
        self.window._monitor = self._real_monitor
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def wait_until(self, predicate, timeout: float = 3.0) -> None:
        if predicate():
            return
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(int(timeout * 1000))

        def poll() -> None:
            if predicate():
                loop.quit()
            else:
                QTimer.singleShot(10, poll)

        QTimer.singleShot(0, poll)
        loop.exec()
        timer.stop()
        self.assertTrue(predicate(), "condition did not become true in time")

    def test_monitor_stop_called_on_close(self) -> None:
        """closeEvent must call monitor.stop() before cancelling the worker."""
        self.window._monitor.stop = MagicMock()
        self.window.close()
        self.window._monitor.stop.assert_called()

    def test_monitor_stop_called_on_directory_change(self) -> None:
        """Changing the selected folder must stop the current monitor."""
        import tempfile
        self.window._monitor.stop = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            self.window.settings_page.path_input.setText(tmp)
        self.window._monitor.stop.assert_called()

    def test_closing_flag_prevents_monitor_update(self) -> None:
        """_on_monitor_changes must do nothing when _closing is True."""
        self.window._closing = True
        events = frozenset([ChangeEvent("created", "/tmp/x.txt")])
        self.window._on_monitor_changes(events)
        # No worker should have started
        self.assertIsNone(self.window._active_worker)

    def test_pending_events_queued_during_active_worker(self) -> None:
        """Events arriving while a worker is active are stored for later."""
        hold = threading.Event()

        def slow_task(cancel: threading.Event) -> object:
            hold.wait(5)
            return None

        self.window._start_worker("scan", slow_task)
        self.assertIsNotNone(self.window._active_worker)

        events = frozenset([ChangeEvent("created", "/tmp/queued.txt")])
        self.window._on_monitor_changes(events)

        self.assertEqual(self.window._pending_monitor_events, events)
        # Clean up
        hold.set()

    def test_monitor_status_hidden_by_default(self) -> None:
        self.assertTrue(self.window.settings_page.monitor_status.isHidden())

    def test_set_monitor_status_shows_and_hides_label(self) -> None:
        self.window.settings_page.set_monitor_status(True)
        self.assertFalse(self.window.settings_page.monitor_status.isHidden())
        self.assertIn(
            "Monitoring active",
            self.window.settings_page.monitor_status.text(),
        )
        self.window.settings_page.set_monitor_status(False)
        self.assertTrue(self.window.settings_page.monitor_status.isHidden())

    def test_pending_events_cleared_on_closing(self) -> None:
        """Pending monitor events must be discarded when closeEvent runs."""
        self.window._pending_monitor_events = frozenset(
            [ChangeEvent("created", "/tmp/x.txt")]
        )
        self.window._monitor.stop = MagicMock()
        self.window.close()
        self.assertIsNone(self.window._pending_monitor_events)

    def test_reconciliation_defers_while_user_worker_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.window._monitor._root = Path(tmp)
            self.window._worker_controller.active_worker = MagicMock()
            self.window._request_reconciliation()
            self.assertTrue(self.window._reconciliation_pending)
            self.window._worker_controller.active_worker = None
            with patch.object(self.window, "_start_worker", return_value=True) as start:
                self.window._start_deferred_work()
            self.assertEqual(start.call_args.args[0], "reconcile")

    def test_monitor_failure_uses_bounded_recovery_timer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.window._monitor._root = Path(tmp)
            self.window._on_monitor_health("failed")
            self.assertTrue(self.window._recovery_timer.isActive())
            self.window._recovery_timer.stop()

    def test_application_refresh_defers_and_coalesces_while_worker_is_active(self) -> None:
        self.window._worker_controller.active_worker = MagicMock()
        start_menu = Path("C:/Start Menu/Programs")
        event = ChangeEvent("modified", str(start_menu / "Editor.lnk"))
        with patch(
            "snapseek.desktop.configured_application_directories",
            return_value=(start_menu,),
        ):
            self.window._on_application_monitor_changes(frozenset([event]))
            self.window._on_application_monitor_changes(frozenset([event]))
        self.assertTrue(self.window._application_refresh_pending)
        self.window._worker_controller.active_worker = None
        with patch.object(self.window, "_start_worker", return_value=True) as start:
            self.window._start_deferred_work()
        self.assertEqual(start.call_args.args[0], "applications")
        self.assertFalse(self.window._application_refresh_pending)

    def test_nested_desktop_events_do_not_refresh_application_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            nested = desktop / "Projects"
            nested.mkdir(parents=True)
            with patch(
                "snapseek.desktop.configured_application_directories",
                return_value=(desktop,),
            ), patch.object(self.window, "_request_application_refresh") as request:
                self.window._on_application_monitor_changes(
                    frozenset(
                        [
                            ChangeEvent(
                                "modified",
                                str(nested / "nested.lnk"),
                            )
                        ]
                    )
                )
            request.assert_not_called()

    def test_top_level_desktop_events_refresh_application_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "Desktop"
            desktop.mkdir()
            with patch(
                "snapseek.desktop.configured_application_directories",
                return_value=(desktop,),
            ), patch.object(self.window, "_request_application_refresh") as request:
                self.window._on_application_monitor_changes(
                    frozenset(
                        [ChangeEvent("created", str(desktop / "new.lnk"))]
                    )
                )
            request.assert_called_once_with()

    def test_application_refresh_failure_retries_without_observer_recovery(self) -> None:
        self.window._application_refresh_pending = False
        with patch.object(self.window, "_start_deferred_work"):
            self.window._worker_completed(
                WorkerOutcome("applications", error=RuntimeError("temporary"))
            )
        self.assertTrue(self.window._application_refresh_pending)
        self.assertTrue(self.window._application_refresh_timer.isActive())
        self.assertFalse(self.window._application_recovery_timer.isActive())
        self.window._application_refresh_timer.stop()


if __name__ == "__main__":
    unittest.main()
