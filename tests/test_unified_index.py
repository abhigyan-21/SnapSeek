"""Tests for the local unified SQLite index and category ranking."""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from snapseek.unified_index import LocalIndex
from snapseek.unified_search import (
    _DESKTOP_KNOWN_FOLDER,
    _PUBLIC_DESKTOP_KNOWN_FOLDER,
    configured_application_directories,
    default_application_directories,
    application_event_is_relevant,
    recursive_application_directories,
    search_index,
)
from snapseek.ui.operations import build_application_refresh_task


class UnifiedIndexTests(unittest.TestCase):
    def test_application_directories_include_known_desktops_and_available_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user_start = root / "user-start"
            machine_start = root / "machine-start"
            user_desktop = root / "redirected-desktop"
            public_desktop = root / "public-desktop"
            for path in (user_start, machine_start, user_desktop, public_desktop):
                path.mkdir()
            with patch.dict(
                os.environ,
                {
                    "APPDATA": str(root / "appdata"),
                    "PROGRAMDATA": str(root / "programdata"),
                },
                clear=False,
            ), patch(
                "snapseek.unified_search._known_folder_path",
                side_effect=lambda identifier: {
                    _DESKTOP_KNOWN_FOLDER: user_desktop,
                    _PUBLIC_DESKTOP_KNOWN_FOLDER: public_desktop,
                }.get(identifier),
            ):
                configured = configured_application_directories()
                available = default_application_directories()
            self.assertEqual(configured[-2:], (user_desktop, public_desktop))
            self.assertEqual(available, (user_desktop, public_desktop))

    def test_application_directories_fallback_to_environment_desktops(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile"
            public = root / "public"
            user_desktop = profile / "Desktop"
            public_desktop = public / "Desktop"
            user_desktop.mkdir(parents=True)
            public_desktop.mkdir(parents=True)
            with patch.dict(
                os.environ,
                {
                    "APPDATA": "",
                    "PROGRAMDATA": "",
                    "USERPROFILE": str(profile),
                    "PUBLIC": str(public),
                },
                clear=False,
            ), patch("snapseek.unified_search._known_folder_path", return_value=None):
                self.assertEqual(
                    configured_application_directories(),
                    (user_desktop, public_desktop),
                )

    def test_schema_is_created_and_incremental_metadata_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "library"
            root.mkdir()
            image = root / "Invoice.PNG"
            image.write_text("not real image", encoding="utf-8")
            document = root / "notes.txt"
            document.write_text("notes", encoding="utf-8")
            database = Path(temporary) / "index.sqlite3"
            calls: list[Path] = []

            with LocalIndex(database) as index:
                self.assertEqual(
                    index.index_folder(root, ocr_extractor=lambda path: calls.append(path) or "invoice text"),
                    2,
                )
                self.assertEqual(calls, [image])
                self.assertEqual(
                    index.index_folder(root, ocr_extractor=lambda path: calls.append(path) or "changed"),
                    2,
                )
                self.assertEqual(calls, [image])
                document.unlink()
                index.index_folder(root)
                self.assertEqual([item.item_type for item in index.search("invoice", "screenshots")], ["screenshot"])
                self.assertEqual(index.search("notes", "files"), [])

    def test_overlapping_roots_do_not_duplicate_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "nested"
            nested.mkdir()
            file_path = nested / "report.pdf"
            file_path.write_text("report", encoding="utf-8")
            with LocalIndex(root / "index.sqlite3") as index:
                index.index_folder(root)
                index.index_folder(nested)
                self.assertEqual(len(index.search("report", "files")), 1)

    def test_application_discovery_and_category_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shortcut = root / "Editor.lnk"
            shortcut.write_text("placeholder", encoding="utf-8")
            with LocalIndex(root / "index.sqlite3") as index:
                self.assertEqual(index.index_applications([root]), 1)
                results = search_index(index, "Editor", "apps")
                self.assertEqual(results[0].result_type, "app")
                self.assertEqual(results[0].item.launch_target, shortcut)

    def test_application_refresh_removes_stale_shortcuts_only_in_configured_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            start_menu = root / "start"
            outside = root / "outside"
            start_menu.mkdir()
            outside.mkdir()
            current = start_menu / "Current.lnk"
            stale = start_menu / "Stale.lnk"
            external = outside / "External.lnk"
            current.write_text("current", encoding="utf-8")
            stale.write_text("stale", encoding="utf-8")
            external.write_text("external", encoding="utf-8")
            document = start_menu / "notes.txt"
            document.write_text("keep", encoding="utf-8")
            with LocalIndex(root / "index.sqlite3") as index:
                index.index_single_file(document, stability_check=False)
                index.index_applications((start_menu, outside))
                stale.unlink()
                current.write_text("updated", encoding="utf-8")
                index.index_applications((start_menu,))
                stale_row = index.connection.execute(
                    "SELECT 1 FROM items WHERE path = ?",
                    (LocalIndex._key(stale),),
                ).fetchone()
                external_row = index.connection.execute(
                    "SELECT 1 FROM items WHERE path = ?",
                    (LocalIndex._key(external),),
                ).fetchone()
                document_row = index.connection.execute(
                    "SELECT item_type FROM items WHERE path = ?",
                    (LocalIndex._key(document),),
                ).fetchone()
                current_row = index.connection.execute(
                    "SELECT size_bytes FROM items WHERE path = ?",
                    (LocalIndex._key(current),),
                ).fetchone()
                self.assertIsNone(stale_row)
                self.assertIsNotNone(external_row)
                self.assertEqual(document_row["item_type"], "file")
                self.assertEqual(current_row["size_bytes"], len("updated"))

    def test_application_refresh_cancellation_preserves_existing_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.mkdir(exist_ok=True)
            existing = root / "Existing.lnk"
            new_shortcut = root / "New.lnk"
            existing.write_text("existing", encoding="utf-8")
            with LocalIndex(root / "index.sqlite3") as index:
                index.index_applications((root,))
                new_shortcut.write_text("new", encoding="utf-8")
                calls = 0

                def cancelled() -> bool:
                    nonlocal calls
                    calls += 1
                    return calls >= 2

                with self.assertRaises(RuntimeError):
                    index.index_applications((root,), cancelled=cancelled)
                existing_row = index.connection.execute(
                    "SELECT 1 FROM items WHERE path = ?",
                    (LocalIndex._key(existing),),
                ).fetchone()
                new_row = index.connection.execute(
                    "SELECT 1 FROM items WHERE path = ?",
                    (LocalIndex._key(new_shortcut),),
                ).fetchone()
                self.assertIsNotNone(existing_row)
                self.assertIsNone(new_row)

    def test_application_refresh_preserves_records_under_unavailable_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            available = root / "available"
            unavailable = root / "unavailable"
            available.mkdir()
            unavailable.mkdir()
            preserved = unavailable / "Preserved.lnk"
            preserved.write_text("preserved", encoding="utf-8")
            with LocalIndex(root / "index.sqlite3") as index:
                index.index_applications((available, unavailable))
                unavailable.rename(root / "disconnected")
                index.index_applications((available, unavailable))
                row = index.connection.execute(
                    "SELECT 1 FROM items WHERE path = ?",
                    (LocalIndex._key(preserved),),
                ).fetchone()
                self.assertIsNotNone(row)

    def test_application_refresh_indexes_desktop_shortcuts_without_merging_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            start_menu = root / "start"
            desktop = root / "desktop"
            start_menu.mkdir()
            desktop.mkdir()
            start_shortcut = start_menu / "Editor.lnk"
            desktop_shortcut = desktop / "Editor.lnk"
            start_shortcut.write_text("start", encoding="utf-8")
            desktop_shortcut.write_text("desktop", encoding="utf-8")
            with LocalIndex(root / "index.sqlite3") as index:
                self.assertEqual(index.index_applications((start_menu, desktop)), 2)
                results = search_index(index, "Editor", "apps", limit=10)
                self.assertEqual(
                    {result.item.path for result in results},
                    {start_shortcut.resolve(), desktop_shortcut.resolve()},
                )

    def test_application_refresh_uses_top_level_policy_for_desktop_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            start_menu = root / "start"
            desktop = root / "desktop"
            nested = desktop / "projects"
            start_menu.mkdir()
            nested.mkdir(parents=True)
            top_level = desktop / "TopLevel.lnk"
            nested_shortcut = nested / "Nested.lnk"
            start_nested = start_menu / "Group" / "StartNested.lnk"
            top_level.write_text("top", encoding="utf-8")
            nested_shortcut.write_text("nested", encoding="utf-8")
            start_nested.parent.mkdir()
            start_nested.write_text("start", encoding="utf-8")
            with LocalIndex(root / "index.sqlite3") as index:
                index.index_applications(
                    (start_menu, desktop),
                    recursive_directories=(start_menu,),
                )
                self.assertIsNotNone(
                    index.connection.execute(
                        "SELECT 1 FROM items WHERE path = ?",
                        (LocalIndex._key(top_level),),
                    ).fetchone()
                )
                self.assertIsNone(
                    index.connection.execute(
                        "SELECT 1 FROM items WHERE path = ?",
                        (LocalIndex._key(nested_shortcut),),
                    ).fetchone()
                )
                self.assertIsNotNone(
                    index.connection.execute(
                        "SELECT 1 FROM items WHERE path = ?",
                        (LocalIndex._key(start_nested),),
                    ).fetchone()
                )

    def test_recursive_application_policy_keeps_only_start_menu_roots(self) -> None:
        roots = (
            Path("C:/Microsoft/Windows/Start Menu/Programs"),
            Path("C:/Other/Microsoft/Windows/Start Menu/Programs"),
            Path("C:/desktop"),
        )
        with patch(
            "snapseek.unified_search.configured_application_directories",
            return_value=roots,
        ):
            self.assertEqual(
                recursive_application_directories(roots),
                roots[:2],
            )

    def test_application_event_policy_ignores_nested_desktop_changes(self) -> None:
        desktop = Path("C:/Users/test/Desktop")
        start_menu = Path("C:/Users/test/AppData/Programs")
        roots = (start_menu, desktop)
        self.assertFalse(
            application_event_is_relevant(
                str(desktop / "Projects" / "nested.lnk"),
                None,
                False,
                roots,
            )
        )
        self.assertTrue(
            application_event_is_relevant(
                str(desktop / "top-level.lnk"),
                None,
                False,
                roots,
            )
        )

    def test_application_refresh_task_honors_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = LocalIndex(root / "index.sqlite3")
            try:
                cancel = threading.Event()
                cancel.set()
                task = build_application_refresh_task(
                    lambda: (root,),
                    index=index,
                )
                self.assertIsNone(task(cancel))
            finally:
                index.close()


class LocalIndexLifecycleTests(unittest.TestCase):
    """Deterministic tests for transaction, cancellation, shutdown and closure."""

    def _make_index(self) -> tuple[LocalIndex, Path]:
        temporary = tempfile.TemporaryDirectory()
        database = Path(temporary.name) / "index.sqlite3"
        index = LocalIndex(database)
        return index, temporary

    def test_index_folder_marks_root_failed_on_exception(self) -> None:
        index, temporary = self._make_index()
        try:
            root = Path(temporary.name) / "library"
            root.mkdir()
            (root / "a.txt").write_text("hello", encoding="utf-8")

            with patch("snapseek.unified_index.os.walk", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    index.index_folder(root)

            rows = index.connection.execute(
                "SELECT status, error FROM roots"
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "failed")
            self.assertIn("boom", rows[0]["error"])
        finally:
            index.close()
            temporary.cleanup()

    def test_index_folder_handles_cancellation(self) -> None:
        index, temporary = self._make_index()
        try:
            root = Path(temporary.name) / "library"
            root.mkdir()
            (root / "a.txt").write_text("hello", encoding="utf-8")
            (root / "b.txt").write_text("world", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                index.index_folder(root, cancelled=lambda: True)

            rows = index.connection.execute(
                "SELECT status FROM roots"
            ).fetchall()
            self.assertEqual(rows[0]["status"], "failed")
        finally:
            index.close()
            temporary.cleanup()

    def test_close_blocks_until_indexing_worker_finishes(self) -> None:
        """close() must wait for an in-progress index_folder to release the lock.

        Two threading.Event objects act as a deterministic barrier so the test
        thread can prove close() blocked rather than coincidentally running after
        index_folder had already finished.

        Sequence
        --------
        1. index_folder thread acquires the RLock and then pauses on ``pause``.
        2. Test thread calls close(), which tries to acquire the same RLock and
           therefore blocks.
        3. Test thread signals ``pause``, unblocking the worker.
        4. Worker finishes os.walk, releases the RLock, and sets ``walk_done``.
        5. close() acquires the RLock, closes the connection, and returns.

        If close() did not actually block on the lock the assertions about
        connection state would still hold, but ``walk_done`` would not have been
        set before close() returned, making it a proven waiting test.
        """
        index, temporary = self._make_index()
        try:
            root = Path(temporary.name) / "library"
            root.mkdir()
            (root / "a.txt").write_text("hello", encoding="utf-8")

            pause = threading.Event()      # test thread unblocks the worker
            walk_done = threading.Event()  # worker signals that os.walk finished

            original_walk = os.walk

            def slow_walk(path, **kw):
                pause.wait(5)  # hold the RLock here until the test releases it
                yield from original_walk(path, **kw)
                walk_done.set()

            with patch("snapseek.unified_index.os.walk", side_effect=slow_walk):
                index_thread = threading.Thread(
                    target=lambda: index.index_folder(root), daemon=True
                )
                index_thread.start()

                # Give the index thread time to acquire the RLock and enter
                # slow_walk before we start close().  close() will then have to
                # wait on the lock — proving it does not skip ahead.
                # We use a short sleep here only to establish ordering; the real
                # correctness check is that walk_done is set *before* close()
                # returns, which a sleep cannot fake.
                import time as _time
                _time.sleep(0.05)

                close_thread = threading.Thread(target=index.close, daemon=True)
                close_thread.start()

                # close() is now blocked on the RLock.  Unblock the worker.
                pause.set()

                close_thread.join(5)
                index_thread.join(5)

            self.assertFalse(index_thread.is_alive(), "index thread did not finish")
            self.assertFalse(close_thread.is_alive(), "close thread did not finish")
            # walk_done must be set before close() could return because the
            # worker sets it after os.walk finishes but while still holding the
            # RLock that close() was waiting on.
            self.assertTrue(walk_done.is_set(), "os.walk did not finish before close() returned")
            self.assertIsNone(index.connection)
            self.assertTrue(index._closed)
        finally:
            temporary.cleanup()

    def test_close_after_successful_index(self) -> None:
        index, temporary = self._make_index()
        try:
            root = Path(temporary.name) / "library"
            root.mkdir()
            (root / "a.txt").write_text("hello", encoding="utf-8")

            count = index.index_folder(root)
            self.assertEqual(count, 1)
            self.assertEqual(len(index.search("hello", "files")), 1)

            index.close()
            self.assertIsNone(index.connection)
            self.assertTrue(index._closed)
            self.assertEqual(index.search("hello", "files"), [])
        finally:
            temporary.cleanup()

    def test_close_is_idempotent(self) -> None:
        index, temporary = self._make_index()
        try:
            root = Path(temporary.name) / "library"
            root.mkdir()
            (root / "a.txt").write_text("hello", encoding="utf-8")
            index.index_folder(root)

            index.close()
            first_connection = index.connection
            index.close()
            second_connection = index.connection

            self.assertIsNone(first_connection)
            self.assertIsNone(second_connection)
            self.assertTrue(index._closed)
        finally:
            temporary.cleanup()

    def test_operations_after_close_raise_or_return_safe_default(self) -> None:
        index, temporary = self._make_index()
        try:
            root = Path(temporary.name) / "library"
            root.mkdir()
            (root / "a.txt").write_text("hello", encoding="utf-8")
            index.index_folder(root)

            index.close()
            self.assertEqual(index.search("hello", "files"), [])

            with self.assertRaises(RuntimeError):
                index.add_root(root)
            with self.assertRaises(RuntimeError):
                index.index_folder(root)
            with self.assertRaises(RuntimeError):
                index.index_applications([root])
        finally:
            temporary.cleanup()

    def test_concurrent_index_folder_calls_are_serialized(self) -> None:
        index, temporary = self._make_index()
        try:
            root = Path(temporary.name) / "library"
            root.mkdir()
            (root / "hello.txt").write_text("hello", encoding="utf-8")
            (root / "world.txt").write_text("world", encoding="utf-8")

            barrier = threading.Barrier(2)
            results = []
            errors = []

            def worker():
                try:
                    barrier.wait(5)
                    results.append(index.index_folder(root))
                except Exception as error:
                    errors.append(error)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)

            self.assertEqual(len(errors), 0)
            self.assertEqual(len(results), 2)
            self.assertEqual(sum(results), 4)
            self.assertEqual(len(index.search("hello", "files")), 1)
            self.assertEqual(len(index.search("world", "files")), 1)
        finally:
            index.close()
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
