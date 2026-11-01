# SnapSeek - Progress

## Completed
- [x] Project setup: basic `pyproject.toml`, `src/snapseek/` package, and `.gitignore`
- [x] Phase 1: screenshot inventory, filesystem error handling, and unit tests
- [x] Phase 2 unit 1: standalone OCR component and mocked unit tests
- [x] Phase 2 unit 2: integrate OCR into the CLI and handle failures
- [x] Phase 2 real OCR check: user reported successful Tesseract OCR on a screenshot
- [x] Phase 3: in-memory keyword search
- [x] Phase 4: TF-IDF ranking over in-memory OCR results
- [x] Phase 5: local semantic search over OCR text
- [x] Phase 6: native PySide6 desktop screenshot-search UI
- [x] Phase 6 branding: approved icon, Qt wordmark, vector navigation icons, and theme
- [x] Phase 7: synthetic retrieval evaluation and documentation
- [x] Desktop UI/UX polish: frosted-blue styling, native title controls, search motion, and clearer session statistics
- [x] Desktop search visibility polish: explicit landing-bar sizing and stronger frosted search/title surfaces
- [x] Desktop restored-scan polish: preserve the complete landing hero instead of hiding it on startup
- [x] Desktop search sizing and glass polish: centered responsive 840px search row with lighter blue Acrylic surfaces
- [x] Desktop glass tint refinement: replaced neutral grey-white surfaces with an icy light-blue palette
- [x] Desktop search transition refinement: keep the landing layout during loading, then animate the bar upward when results arrive
- [x] Desktop result cap: render only the top 10 semantic matches to keep scrolling responsive
- [x] Phase 8 packaging setup: PyInstaller Windows x64 onedir spec and runtime asset inclusion
- [x] Phase 8 local build: generated and directly launched `dist\SnapSeek\SnapSeek.exe` on Windows 11 x64
- [x] Stage 1 modularization: extracted Windows-native window integration into `src\snapseek\platform\windows_window.py`
- [x] Stage 2 modularization: extracted workers, result cards, result grid, and preview dialog into `src\snapseek\ui\`
- [x] Stage 3 modularization: extracted desktop scan/search orchestration and worker lifecycle coordination into `src\snapseek\ui\operations.py`
- [x] Stage 4 modularization: extracted pages, navigation/title-bar presentation, and shared theme/style helpers into `src\snapseek\ui\`

- [x] Stage 5 modularization: removed platform compatibility wrappers from `desktop.py`; `desktop.py` now imports `from snapseek.platform import windows_window` and calls platform functions directly; tests patch `snapseek.platform.windows_window.*` instead of `snapseek.desktop.*`

- [x] Stage 6 shutdown hardening: deferred-close via `event.ignore()`, `waitForDone(2000)` timeout, `_start_worker` centralized closing guard, `_worker_completed` retry, and deterministic shutdown tests
- [x] Desktop application discovery: per-user and Public Desktop `.lnk` roots use Windows Known Folder resolution with safe fallbacks; cached-scan layout restoration is one-shot so search state survives minimize/restore
- [x] Stage 9.1: standalone local PDF extraction with page boundaries, metadata, cancellation, and explicit error/OCR-required statuses
- [x] Stage 9.2: transactional PDF metadata/page persistence with schema migration, incremental fingerprints, monitor events, reconciliation, and transient-failure preservation
- [x] Stage 9.3: SQLite FTS5 PDF keyword search with bounded fallback, page ranking/snippets, Documents category, and Universal Search integration
- [x] Stage 9.4: bounded scanned-PDF rendering and local Tesseract OCR with schema v4 provenance and atomic replacement

## Current implementation
`main.py` remains the command-line entry point: it reads `SNAPSEEK_SCREENSHOT_DIR`, accepts one query, scans supported direct-child images, runs OCR, and uses semantic ranking. `app.py` starts the separate PySide6 desktop app, where the user can choose a folder with a native picker. Both use the shared scanner, OCR, and semantic modules.

The desktop app keeps one scan snapshot and semantic encoder in memory while it runs. Scanning, OCR, and search use Qt worker threads; scan activity, setup failures, image failures, counts, and search status are shown in the UI. The branded frameless title bar uses Qt system movement; Windows native `WM_NCHITTEST` results drive DPI-scaled edge/corner resizing, and the HWND enables `WS_THICKFRAME` with a frame refresh so Windows can start its native resize loop. An 8-pixel very-low-alpha icy rim prevents the layered client’s fully transparent resize pixels from passing mouse input through. Maximized/full-screen windows are excluded. On eligible Windows builds with transparency enabled, it requests blue-tinted Acrylic through `SetWindowCompositionAttribute` and DWM rounded corners; otherwise it uses an opaque icy-blue fallback. The Acrylic tint is `0xFDF0E2` at `0x26` alpha (about 15%); title and raised search/card surfaces use alpha 64/255 (25% surface opacity), with translucent navigation/secondary-control states. This aims to expose more blurred backdrop. AccentPolicy does not expose blur-radius control, and its undocumented API return does not prove the desired visual result. The previously tested documented DWM backdrop showed a uniform gray Qt surface. The latest more-translucent glass appearance still needs manual verification. Title-control focus styling distinguishes keyboard focus from mouse focus. The valid-query transition animates the compact search bar; Reduce motion skips that transition for the current session. Search APIs and rankings remain unchanged. Stats use vector icons and an indeterminate scan indicator. OCR records, counts, and embeddings are not persisted.

Phase 7 adds a standard-library generator for 16 deterministic synthetic PNG screenshots and a 12-query stable-ID relevance manifest. The offline evaluation module adapts the existing keyword, TF-IDF, and semantic return types and calculates Precision@5/Recall@5 and Precision@10/Recall@10. The live runner requires every generated screenshot to OCR successfully and the configured semantic model to load before it prints results. No UI or search API behavior changed.

## Known limitations
- The package uses a `src/` layout; run from a checkout with `PYTHONPATH=src` or an editable install.
- PDF records are persisted locally in SQLite schema version 4. Unchanged PDFs skip extraction using size and `st_mtime_ns`; forced integrity checks use streaming SHA-256. Documents search uses FTS5 or a bounded SQL fallback and returns page-level snippets. Scanned pages are rendered and OCRed locally through pypdfium2 and Tesseract; pages with meaningful selectable text are not OCRed. Password handling, semantic embeddings, and exact-page viewer navigation remain future stages.
- The selected desktop folder, scan results, statistics, and Reduce motion preference last for the running app only. Nothing searchable or statistical is persisted across restarts. `SNAPSEEK_SCREENSHOT_DIR` is used as the initial folder when available; the CLI continues to require it.
- FastEmbed downloads model files on first use if they are not cached. The model cache is local to the machine; semantic search cannot start offline unless the model has already been downloaded.
- The app reuses the encoder during its process but recomputes document vectors for each submitted query. No OCR text, scan index, or vector cache persists between runs.
- A running Tesseract/model operation cannot always be interrupted mid-call. Closing the window requests cooperative cancellation between operations; workers do not update widgets directly.
- OCR/search cover text captured by OCR, not visual content OCR misses. Semantic results have no relevance threshold and can include weak matches.
- The Windows x64 onedir build is defined by `SnapSeek.spec`; PyInstaller 6.22.3 built it successfully with Python 3.13.7 after limiting OpenBLAS/OMP to one thread. The executable opened directly and the UI assets, Qt Windows/SVG plugins, and ONNX Runtime binaries were inspected in the output. This is a local development build, not a clean-machine qualification or installer. Tesseract remains a separate installation; the OCR component preserves explicit executable configuration, uses PATH, then checks the standard Windows install location. Non-standard installs still need PATH or explicit configuration. The FastEmbed model is not bundled.
- The current machine has Tesseract at the standard install path. No FastEmbed cache was found in the default cache locations; the build/runtime check did not trigger a model download. First semantic use still requires internet if the model is not already cached.
- The packaged GUI opened its Settings page, accepted a test folder path, and opened the native folder picker. A full scan was not run because no model cache was present and a model download was intentionally avoided. A fresh Windows machine/runtime smoke test and installer remain future work.
- The user reported a successful real Tesseract OCR check on a screenshot. Real semantic search has not been run through the desktop UI during Phase 6.
- The synthetic benchmark is intentionally small and does not establish retrieval quality on personal screenshot collections; live metrics also depend on Tesseract and model versions.

Stage 1 now gives `windows_window.py` ownership of Win32 ctypes structures,
AppUserModelID setup, single-instance activation, Acrylic/DWM composition,
resize-frame setup, foreground activation, DPI-aware hit testing, and native
message interpretation. `desktop.py` keeps Qt UI orchestration and a thin
`nativeEvent()` adapter, while compatibility wrappers preserve existing imports.
`windows_hotkey.py` remains the sole owner of global shortcut registration.

Stage 2 gives `ui\workers.py` ownership of cancellable QRunnable execution and
outcome/error signals; `ui\result_cards.py` owns screenshot and unified result
presentation; `ui\result_grid.py` owns deferred batched rendering and responsive
card layout; and `ui\dialogs.py` owns screenshot preview presentation.
`desktop.py` continues to compose pages and imports these names as compatibility
re-exports. No search, OCR, index, or platform behavior changed.

Stage 3 gives `ui\operations.py` ownership of cached snapshot restoration,
scan/search task construction, scan persistence, and active worker coordination.
`MainWindow` applies outcomes to widgets but no longer embeds the scan/search
closures. The audit found no safe file, dependency, asset, or obsolete-test
deletions; referenced packaging resources, evaluation data, compatibility
imports, and planned components were retained.

Stage 4 gives `ui\pages.py` ownership of Home, Folder, Stats, and Settings
construction; `ui\navigation.py` ownership of the title bar and window controls;
and `ui\theme.py` ownership of shared visual helpers and stylesheet generation.
`MainWindow` now composes these modules and retains application-level lifecycle,
native event, navigation, and outcome coordination.

## Validation history
- Phase 1 scanner tests use temporary directories and cover supported/unsupported extensions, case normalization, sorting, empty/missing directories, ignored subfolders, disappearing files, and listing errors.
- Phase 2–4: full suite passed 46 Phase 1–4 tests. OCR was mocked; this did not rerun the user-reported real Tesseract check.
- Phase 4 focused tests: `test_search.py` passed 15 tests; `test_main.py` passed 9 tests.
- Phase 5: FastEmbed 0.9.1 and ONNX Runtime 1.31.1 were installed; the full suite passed 62 tests using fake embeddings. A later separately reported real-model smoke test completed in the prior phase, not through the desktop UI.
- Phase 6 dependency install: `.venv\Scripts\python.exe -m pip install "PySide6>=6.8.1,<7"` succeeded with PySide6 6.12.0. The Streamlit dependency/artifacts from the interrupted earlier UI direction were removed; Streamlit was uninstalled from `.venv`.
- Phase 6 focused desktop workflow tests: 7 passed. Headless desktop tests: 10 passed. Tests use fake embeddings and mocked OCR; no real model download or real OCR was performed by them.
- Phase 6 full suite: `$env:PYTHONPATH = "src"; $env:QT_QPA_PLATFORM = "offscreen"; $env:OPENBLAS_NUM_THREADS = "1"; $env:OMP_NUM_THREADS = "1"; & .\.venv\Scripts\python.exe -m unittest discover -s tests -q` ran 79 tests and passed (`PYTHON_EXIT=0`). Expected warning output came from tests exercising background failure handling.
- Phase 6 `git diff --check` completed with exit code 0; Git reported only LF-to-CRLF working-copy warnings.
- Phase 6 branding: added PNG/ICO logo integration and four SVG navigation icons. The icon path resolver supports both the project root and PyInstaller's `_MEIPASS`; package builds must include `assets/logo` and `assets/icons` as data. GUI branding tests run headlessly.
- Qt test teardown cleanup: five separate desktop runs (15 tests each) and five separate full-suite runs (88 tests each) printed `OK` and exited with code 0. This replaced the earlier intermittent shutdown-crash observations for those validation runs.
- Phase 7 focused evaluation tests: 9 passed, process exit code 0. Full suite: 97 passed, process exit code 0.
- UI/UX focused desktop tests: 23 passed, process exit code 0; desktop workflow tests: 7 passed, process exit code 0. Tests use the offscreen Qt platform and do not verify native backdrop rendering.
- UI/UX full suite: 105 tests passed, printed `OK`, process exit code 0. The suite uses mocked OCR/fake embeddings and is not a live model or native-window visual test.
- Phase 7 generated all 16 synthetic PNGs locally and completed the live 12-query evaluation. Runtime versions were Python 3.13.7, pytesseract 0.3.13, Tesseract 5.5.3.20260724, scikit-learn 1.9.1, FastEmbed 0.9.1, and ONNX Runtime 1.31.1. Macro Precision@5/Recall@5 and Precision@10/Recall@10 were keyword 0.017/0.083 and 0.008/0.083, TF-IDF 0.250/1.000 and 0.125/1.000, and semantic 0.250/1.000 and 0.125/1.000. These describe only the synthetic set.
- UI refinement verification: focused desktop tests ran 24 tests and passed (exit code 0); desktop workflow tests ran 7 and passed (exit code 0); full suite ran 106 and passed with `OK` (exit code 0). The native startup check on Windows 11 build 22631 with PySide6/Qt 6.12.0 reported the Acrylic request enabled on the Windows platform. The computer-use surface did not expose the test window for a visual screenshot, so this does not prove that blur is visibly rendered; manual steps are in `README.md`.
- Backdrop follow-up: after the user requested a closer match to the frosted-glass reference, the Acrylic tint alpha was reduced to 25% (`0x40`); translucent title and card/search surfaces were also lightened to 176/255 and 186/255. Unit tests verify these settings; the combined native appearance remains unverified and must be checked on the user's Windows desktop.
- Latest translucent-surface verification: desktop tests ran 29 tests, workflow tests 7, and the full suite 111; all printed `OK` and exited 0. The desktop was restarted with the revised 25% tint and lighter child surfaces. Native appearance still requires visual confirmation.
- User requested stronger glass: tint alpha now 0x26 (about 15%); title and raised search/card surface alpha is 64/255 (25% opacity) with translucent navigation and secondary controls. Desktop tests: 29 passed (exit code 0); workflow tests: 7 passed (exit code 0); full suite: 111 passed (exit code 0). Native visual confirmation is pending.
- Window-interaction follow-up: Windows hit testing now returns native non-client resize codes for all four edges and corners using the HWND rectangle and per-window DPI; the OS handles resize cursor, drag, and boundary behavior. Title controls keep a keyboard-only focus ring and use subtle mouse hover/press states. Automated helpers and headless window-state tests pass; physical resizing, system drag, and taskbar restoration still need Windows manual QA.
- Resize correction: live HWND inspection showed `WS_THICKFRAME` was absent, and the layered window passed mouse input through alpha-zero edge pixels. SnapSeek now enables and refreshes the native sizing frame and paints a nearly transparent 8-pixel resize rim. The running HWND reports `WS_THICKFRAME=True`; sent `WM_NCHITTEST` checks returned the expected codes for all eight edge/corner regions. The runner has no interactive cursor, so physical pointer resizing still needs the developer's direct check. Latest desktop tests: 29 passed (exit code 0); workflow tests: 7 passed (exit code 0); full suite: 111 passed (exit code 0); `git diff --check`: exit code 0.
- Windows activation correction: `WM_MOUSEACTIVATE` returns `MA_ACTIVATE`, and the Qt application event filter makes one `raise_()`/`activateWindow()` request when an inactive SnapSeek window receives a click on one of its widgets. Active-window clicks do not repeat activation; no always-on-top style or polling/API loop is used. The activation tests cover the native response, child-widget mouse event, active-window guard, and absence of `WindowStaysOnTopHint`. Desktop tests: 33 passed; full suite: 115 passed; `git diff --check`: exit code 0. Rebuilt `dist\SnapSeek\SnapSeek.exe` with the activation fix and verified that an actual mouse click brought the packaged app to foreground while a temporary test window was active.
- `git diff --check` completed with exit code 0. The opaque fallback uses `#e8f2fc`; Windows transparency disabled or High Contrast uses that fallback. New SVG assets cover Stats, search, window controls, Settings visuals, and the Reduce motion switch.
- Packaging precheck: Python 3.13.7, Windows AMD64, 64-bit; the declared `packaging` extra was present but PyInstaller was initially missing. Installed the already-declared `pyinstaller>=6.11,<7` into `.venv` (resolved to 6.22.3). The first build attempt exited non-zero because OpenBLAS could not allocate its default thread pool during dependency scanning; the retry used `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1` and exited 0. The bundle contains `dist\SnapSeek\SnapSeek.exe`, runtime icons and SVGs, `qwindows.dll`, `qsvg.dll`, Qt DLLs, and ONNX Runtime binaries. The packaged window opened directly with title `SnapSeek`; GUI folder-path entry and folder-picker opening were verified. The model/cache and actual scan/search were not exercised.

## Next task
Complete final desktop QA, including a manual Windows check of Acrylic blur, native window activation, dragging/resizing, maximize/restore, and text contrast with transparency effects enabled and disabled. Headless tests do not prove the blur is visible; Microsoft's documentation recommends against `SetWindowCompositionAttribute` in favor of `DwmSetWindowAttribute`, while the specific AccentPolicy Acrylic state has no stable public contract. Clean-machine packaging validation, installers, and GitHub Releases remain later work.
- Phase 8 unified search core implemented: versioned local SQLite indexing for selected folders, incremental metadata reuse, deletion cleanup, overlap deduplication, Start Menu shortcut discovery, category-aware file/app ranking, native opening, persisted category selection, and optional Windows global hotkey.
- Phase 8 UI routes Screenshots, Files, Apps, and All through separate engines while preserving the existing screenshot UI, animations, CLI, OCR, semantic search, and evaluation behavior.
- Phase 8 remaining work: continuous Windows filesystem monitoring, persistent screenshot embedding blobs, true typing debounce/cancellation, configurable shortcut UI (environment configuration exists), calibrated cross-category ranking, and manual Windows shortcut/large-library validation.
- Stage 1 limitation: native behavior is mocked in automated tests; Acrylic rendering and global shortcut behavior still require manual validation on Windows.
- Stage 2 validation: focused worker/UI tests and the existing desktop tests must be rerun after extraction; Qt native rendering remains outside headless coverage.
- Stage 4 remaining debt: `MainWindow` still owns application-level lifecycle, native-window event handling, navigation state, styling application, and user-facing outcome presentation; these remain intentionally coordinated there.
- Stage 3 validation: isolated focused regression checks passed; one combined Qt-focused run experienced a shared-thread-pool timeout, while the complete sequential suite passed 143 tests with exit code 0. `desktop.py` is now 1,608 lines. `git diff --check` passed. No files were deleted because repository-wide reference, packaging, asset, test, and planned-component checks found no safe deletion candidate.
- Stage 4 validation: focused UI/page tests passed 54 tests with exit code 0; the complete sequential suite passed 146 tests with exit code 0; application imports and `py_compile` passed with exit code 0; `git diff --check` passed. `desktop.py` is now 679 lines. No files were deleted; all assets, tests, packaging resources, and compatibility exports remain required or intentionally retained.
- Desktop scan optimization implemented: OCR now uses bounded parallel workers, unchanged files reuse metadata-validated cached text, and the selected folder/OCR records persist under `%LOCALAPPDATA%\SnapSeek\state.json`. The semantic model is loaded lazily on first search. Focused and full test suites pass.
- Stage 5 validation: removed 11 compatibility wrappers and `_platform_compatibility_context()` from `desktop.py`; replaced with direct `windows_window.*` calls via `from snapseek.platform import windows_window`. Updated `test_desktop.py` to patch `snapseek.platform.windows_window.*` instead of `snapseek.desktop.*`. Full sequential suite passed 146 tests with exit code 0. `py_compile` passed. `git diff --check` passed. `desktop.py` is now 564 lines (down from 679).
- Stage 6 shutdown hardening: `_start_worker` now returns `False` immediately when `_closing` is `True` (centralized guard). `closeEvent` cancels the active worker, calls `QThreadPool.globalInstance().waitForDone(2000)`, and accepts the event only when the return value is `True`. If `False` (timeout), `event.ignore()` keeps the window and database alive; `_worker_completed` detects `_closing=True` on the next completion and retries `self.close()`. `QThreadPool` added to the top-level `PySide6.QtCore` import. `test_close_blocks_until_indexing_worker_finishes` strengthened with an `os.walk` patch barrier. `DesktopShutdownTests` class added with 7 deterministic tests. Full suite: 160 tests passed, exit code 0. `py_compile` exit code 0. `git diff --check` exit code 0.
- [x] Stage 7 filesystem monitoring: continuous automatic index updates via watchdog `WindowsApiObserver` (`ReadDirectoryChangesW`); 250ms debounce with `SimpleQueue`+`QTimer` GUI marshalling; targeted `LocalIndex` APIs (`index_single_file`, `index_single_directory`, `remove_item`, `remove_items_under`); `build_watch_task` for event-driven incremental indexing; `FolderMonitor` in `src/snapseek/filesystem_monitor.py`; `SettingsPage.set_monitor_status()` indicator; `stability_check=False` in `_apply_change_event`; monitor started deferred via `QTimer.singleShot(0)` after successful scan; `WorkerController.cancel_watch()` lets scan/search preempt watch workers; worker completion tokens ignore stale cancelled-worker signals and clear the active slot before publishing completion; empty screenshot searches skip unnecessary model loading; `watchdog>=6.0,<7` declared in `pyproject.toml`; `watchdog` added to `SnapSeek.spec` hidden imports. `WatchWorkerPreemptionTests` and controller lifecycle regression tests cover preemption and stale completion handling. Full suite: 239 tests passed, exit code 0, 20.061s runtime. `git diff --check` exit code 0.
- [x] Stage 7A file-integrity protection: targeted create/modify/rename-destination indexing requires consecutive unchanged size and `st_mtime_ns` observations; disappearing or changing files are deferred without replacing valid records; transient access failures use bounded cancellation-aware retry/backoff; permanent errors are not retried; rename source records are removed only after successful destination indexing. Focused filesystem-monitor suite: 79 tests passed. Combined Stage 7A regression modules: 155 tests passed. `compileall` and `git diff --check` passed.
- [x] Stage 7B reconciliation and observer recovery: conservative five-minute GUI reconciliation timer runs `LocalIndex.index_folder()` through `WorkerController`; reconciliation defers behind scans, searches, and watch updates; `FolderMonitor` health polling detects dead observers and missing roots while retaining the configured root; recovery uses bounded exponential backoff, reconciles before restarting an observer, and responds to application reactivation events. Focused monitor/reconciliation suite: 85 tests passed.
- [x] Stage 7C.1 application refresh: `LocalIndex.index_applications()` now transactionally discovers current `.lnk` shortcuts, upserts additions/modifications, removes stale app records only beneath the supplied Start Menu roots, preserves other item types and out-of-scope app records, and supports cancellation. `build_application_refresh_task()` provides the cancellable operations-layer task. Full suite: 256 tests passed.
- [x] Stage 7C.2 Start Menu monitoring: an independent multi-root `FolderMonitor` watches available per-user and machine-wide Start Menu roots, retains unavailable roots for independent recovery, debounces shortcut events, coalesces refresh requests behind user work, retries refresh failures without restarting healthy observers, and stops safely during shutdown.

## Known limitations (Stage 7 additions)
- Filesystem monitoring uses `watchdog 6.0.0` backed by `ReadDirectoryChangesW` on Windows. The observer runs as a daemon thread; SnapSeek never forcibly terminates it.
- Monitor is started after each successful scan. If the monitored folder becomes unavailable (network share disconnect, drive ejection), `watchdog` may log an error and stop delivering events silently. The manual "Scan / Rescan" button remains the recovery mechanism.
- Start Menu and Desktop shortcut refresh is monitored independently from the selected screenshot folder. Unavailable Start Menu roots retain their existing application records until a later successful enumeration.
- Periodic full reconciliation (catching missed events after wake-from-sleep, etc.) is not yet implemented. Manual Scan / Rescan is the current reconciliation path.
- Live manual validation of real-time index updates on Windows still required.
- Stage 7A does not yet recover a stopped observer, reconcile missed events automatically, refresh application shortcuts, or handle sleep/resume and disconnected-drive recovery; these remain later Stage 7 work.
- Stage 7B recovery is conservative: a missing root is retried while the application remains open, but application shortcut indexing is still not part of reconciliation. Native Windows suspend/resume and real disconnected-drive behavior still require manual validation.
- Stage 7C.2 adds automatic Start Menu monitoring and scheduling, but live Windows validation remains outstanding.
- Stage 7C.2 still requires real Windows validation for Start Menu observer behavior, root permissions, shortcut installation/removal, and shutdown during recovery.
- Desktop shortcut support is implemented. Start Menu roots remain recursive, while Desktop roots enumerate only top-level `.lnk` files so unrelated Desktop folders are not traversed. Application monitor filtering follows the same policy for refresh scheduling. The full 269-test suite passes with this policy.
- [x] Stage 9.1 PDF text extraction: standalone `pypdf`-based page-preserving extraction with metadata, cancellation, source integrity, and explicit empty/OCR-required/encrypted/corrupt/unsupported/inaccessible statuses. PDF indexing, search, and scanned-page OCR remain future work.
