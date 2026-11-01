# SnapSeek — Technical Decisions

> Purpose: Record technical decisions, their rationale, alternatives, and status.
>
> **Status rule:** `Proposed` means it still needs discussion. `Accepted` means we have agreed to it. `Superseded` means a later decision replaced it. Do not treat proposed items as final.

## Decision log

### D-001 — Use Python for the application
- **Status:** Accepted; Python 3.11–3.13 range confirmed by user for Phase 1
- **Context:** SnapSeek needs filesystem discovery, OCR, information retrieval, and a lightweight UI.
- **Decision:** Use Python 3.11, 3.12, or 3.13 as the primary language.
- **Rationale:** Python is the project language; the supported range was confirmed for Phase 1.
- **Alternatives considered:** Not evaluated yet.
- **Consequences:** Keep implementation and examples Python-first.

### D-002 — Process screenshots locally by default
- **Status:** Accepted by PRD
- **Context:** Screenshot collections may contain personal or sensitive information.
- **Decision:** Process screenshots locally and do not require a paid external AI API.
- **Rationale:** Privacy and offline/local operation are explicit project requirements.
- **Consequences:** Do not upload screenshots or OCR text to external services. Any future exception requires an explicit discussion.

### D-003 — Preserve original images
- **Status:** Accepted by PRD
- **Context:** The app indexes a user's existing screenshot library.
- **Decision:** Never modify or delete source images. Store their paths and extracted metadata instead of copying image bytes into metadata records.
- **Rationale:** Indexing must be non-destructive and avoid unnecessary duplication.
- **Consequences:** Handle moved or missing files as index/search errors rather than attempting destructive repair.

### D-004 — Build in incremental phases
- **Status:** Accepted by PRD
- **Context:** This is both a working application and a learning project.
- **Decision:** Implement the phases in this order: image inventory, OCR, keyword search, TF-IDF, semantic search, native desktop UI, evaluation/documentation.
- **Rationale:** Each phase establishes a testable baseline for the next. More advanced search must not conceal basic indexing problems.
- **Consequences:** Do not implement OCR, TF-IDF, embeddings, or the UI during the initial inventory task.

### D-005 — Start with a small module structure
- **Status:** Accepted by PRD
- **Context:** Separating responsibilities is useful, but creating abstractions too early adds complexity.
- **Decision:** Use the `src/snapseek/` package and create modules only when needed: `scanner.py`, `ocr.py`, `search.py`, `semantic.py`, `desktop.py`, `desktop_workflow.py`, and `evaluation.py`.
- **Rationale:** Keep scanning, OCR, search, and coordination separate without creating empty abstractions prematurely.
- **Consequences:** `main.py` is the initial entry point; the architecture can expand as features are implemented.

### D-006 — Keep private data and generated artifacts out of Git
- **Status:** Accepted by PRD
- **Context:** Screenshots, OCR contents, file paths, environments, and caches may be private or machine-specific.
- **Decision:** Do not commit personal screenshots, OCR data, private file paths, virtual environments, or generated caches.
- **Rationale:** Protect privacy and keep the repository reproducible.
- **Consequences:** Configure `.gitignore` before adding real personal data.

### D-007 — Ask before implementation and explain the learning concepts
- **Status:** Proposed workflow based on the user's instruction and PRD
- **Context:** The developer wants to understand the code and technologies, not only receive generated code.
- **Decision:** Before a meaningful implementation, explain the problem and relevant concepts, present important choices, ask clarifying questions, and wait for answers. After implementation, explain the code, test it, and invite the developer to reason about its behavior.
- **Rationale:** Keeps the developer involved and prevents silent assumptions.
- **Consequences:** Codex must not make substantive code changes while waiting for answers. Trivial, clearly specified edits may proceed only when the developer has already authorized them.

### D-008 — Keep architectural and dependency changes explicit
- **Status:** Accepted by PRD
- **Context:** New technologies can increase setup complexity and make learning harder.
- **Decision:** Do not silently add dependencies or make major architectural changes. Explain the need and ask for approval first.
- **Rationale:** Preserve scope and make trade-offs understandable.
- **Consequences:** Keep `requirements.txt` aligned with approved dependencies.

## Accepted Phase 1 decisions

### D-009 — Directory selection belongs in the UI
- **Status:** Accepted by user; Phase 1 path supplied through `SNAPSEEK_SCREENSHOT_DIR`
- **Context:** SnapSeek needs a user interface, and the developer selected option C for screenshot-directory selection.
- **Decision:** Defer interactive directory selection to the planned native desktop UI rather than introducing a command-line argument for the first version.
- **Rationale:** The intended user workflow includes selecting a screenshot directory through the application UI.
- **Consequences:** Phase 1 reads an explicit path from `SNAPSEEK_SCREENSHOT_DIR`; it does not scan unrelated folders. Interactive directory selection remains deferred to the UI phase.

### D-010 — Package layout and supported Python versions
- **Status:** Accepted by user for Phase 1
- **Context:** Phase 1 needs a small importable package and a declared interpreter range.
- **Decision:** Use a `src/snapseek/` package layout and support Python 3.11 through 3.13.
- **Rationale:** This matches the approved Phase 1 architecture and the available project requirements.
- **Consequences:** Source imports require the package to be installed or `src/` to be on `PYTHONPATH` when running from a checkout.

### D-011 — Phase 1 inventory output
- **Status:** Accepted by user for Phase 1
- **Context:** The initial inventory needs a visible result without introducing persistence.
- **Decision:** Print each image's filename, path, normalized extension, and size in bytes, followed by the total count. Do not write an inventory file.
- **Rationale:** This keeps the first phase small and avoids creating generated metadata before persistence is designed.
- **Consequences:** Inventory output is available in the terminal for the current run only.

### D-012 — Handle filesystem changes and scan failures during inventory
- **Status:** Accepted by user for Phase 1 hardening
- **Context:** A supported image or the selected directory can become unavailable while an inventory is being collected.
- **Decision:** If `stat()` reports `FileNotFoundError` for a discovered image, warn with its path, skip that image, and continue. Other filesystem errors encountered during scanning propagate to `main()`, which reports an actionable error and returns a non-zero status.
- **Rationale:** A single vanished image should not discard the rest of the inventory, while permission and directory errors should remain visible instead of being silently suppressed.
- **Alternatives considered:** Abort the whole scan for a vanished image; suppress all filesystem errors.
- **Consequences:** Warnings are written to standard error by the scanner; directory scan errors are presented by the application entry point. The scanner does not catch broad `OSError` exceptions.

### D-013 — Keep OCR as a standalone local component
- **Status:** Accepted by user for Phase 2 implementation unit 1
- **Context:** OCR needs an external engine and should not make image discovery or module imports depend on that engine being present.
- **Decision:** Add `pytesseract` as a Python project dependency and implement `extract_text(ImageMetadata) -> str` in `src/snapseek/ocr.py`. Import the wrapper lazily, distinguish a missing wrapper, missing Tesseract executable, and per-image OCR failures with OCR-specific exceptions, and treat empty output as valid text.
- **Rationale:** This reuses Phase 1 metadata, preserves module boundaries, and permits unit tests to mock OCR without an installed engine.
- **Alternatives considered:** Put OCR into the scanner or import `pytesseract` at module load time.
- **Consequences:** The Tesseract executable remains a separate machine-level prerequisite. This unit does not connect OCR to `main.py`; per-image continuation and end-to-end flow remain for a later integration unit.

### D-014 — Distinguish OCR process launch errors from image errors
- **Status:** Accepted by user for Phase 2 unit 1 hardening
- **Context:** `pytesseract` can surface operating-system errors when its external Tesseract process cannot be launched.
- **Decision:** Map its explicit `TesseractNotFoundError`, and OS errors whose `filename` identifies a path other than the input image, to `TesseractExecutableError`. Keep OS errors naming the source image and Tesseract engine errors as `ImageOCRError`.
- **Rationale:** Report executable setup/permission problems separately from failures reading or processing a particular image, while preserving the original exception as the cause.
- **Alternatives considered:** Wrap every `OSError` as an image failure or every `OSError` as an executable failure.
- **Consequences:** OS errors without a filename are conservatively treated as image-specific; unrelated programming exceptions are not caught.

### D-015 — Integrate OCR in the application entry point
- **Status:** Accepted by user for Phase 2, Unit 2
- **Context:** The standalone OCR function needs to process discovered screenshots while keeping scanning and OCR logic in their existing modules.
- **Decision:** `main.py` calls `scan_images()` once, then `extract_text()` for each returned image and prints successful OCR text beside that image's filename and path. An `ImageOCRError` produces a warning and processing continues. OCR setup errors are reported once, stop further OCR calls, and leave remaining images counted as not processed. Any image-level OCR failure or setup failure returns status 1.
- **Rationale:** This keeps orchestration/output in the entry point, maintains the module boundaries, and makes partial failures visible without abandoning later images.
- **Alternatives considered:** Add OCR to the scanner; stop the complete run at the first image-specific error; return success despite image-level failures.
- **Consequences:** OCR results are held for the run only; no persistence is introduced. At the time of this decision, a real Tesseract run remained outstanding; D-016 records the later user-reported check.

### D-016 — Record the user-reported real OCR check
- **Status:** Confirmed by user report
- **Context:** Earlier Phase 2 documentation said that real Tesseract OCR had not been tested.
- **Decision:** Record that the user successfully ran real Tesseract OCR on a screenshot. Keep this distinct from mocked tests run by the development workflow.
- **Rationale:** Documentation should reflect the reported manual verification without implying that every environment or OCR quality case was independently verified.
- **Consequences:** Real OCR execution and successful recognition are user-reported; automated tests remain deterministic mocked tests, not real OCR tests.

### D-017 — Implement in-memory keyword search
- **Status:** Accepted by user for Phase 3
- **Context:** OCR text needs a simple searchable baseline before persistence or ranking approaches are added.
- **Decision:** Add `src/snapseek/search.py` with an immutable `OCRTextRecord` pairing existing `ImageMetadata` with OCR text. Search supplied records using case-insensitive substring matching; reject blank queries and preserve input order.
- **Rationale:** A small pure function is reusable and testable without rescanning directories or invoking OCR. A positional CLI query provides a simple terminal interface while the screenshot directory remains configured through `SNAPSEEK_SCREENSHOT_DIR`.
- **Alternatives considered:** Put matching into `main.py` or `ocr.py`; rescan or rerun OCR during each match.
- **Consequences:** At the end of Phase 3, `main.py` searched in-memory records and no ranking or persistence was present. D-018 records the Phase 4 TF-IDF behavior; semantic search and UI remain future work.

### D-018 — Add TF-IDF as the Phase 4 internal ranking engine
- **Status:** Accepted by user for Phase 4
- **Context:** SnapSeek's eventual search experience should use one natural query input without asking users to choose a retrieval mode.
- **Decision:** Preserve `search_keyword()` and its tested substring behavior, add `search_tfidf()` using scikit-learn, and route the existing positional query through TF-IDF in Phase 4 without a mode selector. Return typed scored results retaining each `OCRTextRecord`. Include only positive scores, rank descending, and break ties by normalized image path. Use default word unigrams.
- **Rationale:** TF-IDF adds relevance ranking while keeping keyword matching available as a modular component for future hybrid retrieval. The CLI stays simple and OCR results are reused within the current run.
- **Alternatives considered:** Replace keyword search; expose a permanent user-facing mode switch; add automatic hybrid ranking during Phase 4.
- **Consequences:** Phase 4 CLI results can differ from Phase 3 substring matches, while the `search_keyword()` API behavior stays intact. No model, OCR text, or vectorizer is persisted or cached. Semantic and hybrid search remain future work.

### D-019 — Use local semantic embeddings as the Phase 5 default search
- **Status:** Accepted by user for Phase 5
- **Context:** The product uses one natural query input without a search-mode selector, and semantic search should operate locally over OCR text.
- **Decision:** Add FastEmbed with `sentence-transformers/all-MiniLM-L6-v2` and route the existing CLI query through `search_semantic()`. Preserve `search_keyword()` and `search_tfidf()` behavior. Return every nonempty OCR document ranked by cosine similarity, without a score threshold, and break ties by normalized POSIX image path. Load the model before OCR where there are discovered images, batch-encode documents once, encode the query once, and keep generated vectors in memory for the invocation only.
- **Rationale:** FastEmbed provides a small CPU-capable ONNX runtime path that supports the project's Python 3.11–3.13 range. The separate semantic component keeps the existing search functions independently usable and testable.
- **Alternatives considered:** Replace the existing search functions; expose a CLI mode selector; combine TF-IDF and semantic scores before relevance evaluation.
- **Consequences:** The `fastembed>=0.9,<1.0` dependency is added. FastEmbed's downloaded model files are kept in its local model cache for later offline use; SnapSeek does not persist OCR records or embeddings. A missing dependency/model fails clearly without falling back to TF-IDF. Relevance thresholds and hybrid ranking remain deferred until evaluation.

### D-020 — Use PySide6 for the Phase 6 native desktop interface
- **Status:** Accepted by user for Phase 6
- **Context:** SnapSeek's long-term direction is a fast Windows screenshot-search launcher. A native interface avoids building a browser UI that would later need migration.
- **Decision:** Add a separate `app.py` entry point backed by PySide6. Keep `main.py` as the CLI. Use Qt widgets for folder selection and results, `QThreadPool` workers for scanning/OCR/search, one natural-language query, and in-memory session state for scan records and the semantic encoder. Preserve the existing scanner, OCR, and semantic functions.
- **Rationale:** Qt provides a native desktop application, and worker signals can update widgets safely after long-running local operations finish.
- **Alternatives considered:** A local browser UI; mixing UI code into `main.py`; moving scanning or retrieval logic into UI widgets.
- **Consequences:** Add `PySide6>=6.8.1,<7`; PySide6 6.8.1 added Python 3.13 support. The optional `packaging` extra declares PyInstaller for a preliminary Windows build path. Phase 6 does not bundle Tesseract or the FastEmbed model and does not add persistence, global hotkeys, a system tray, or Windows-wide search. Executable packaging remains unverified until a Windows build and runtime test succeeds.

### D-021 — Discover the local Tesseract executable without overriding configuration
- **Status:** Accepted for the desktop OCR setup fix
- **Context:** A desktop process may not inherit temporary shell PATH changes even when Tesseract is installed in its standard Windows location.
- **Decision:** Preserve any non-default `pytesseract.pytesseract.tesseract_cmd`. With pytesseract's default `tesseract` command, first rely on normal PATH lookup; if that fails on Windows, use `C:\Program Files\Tesseract-OCR\tesseract.exe` when it is a file.
- **Rationale:** This supports standard Windows installs while keeping user-configured executable paths authoritative and retaining pytesseract's normal behavior on other platforms.
- **Consequences:** Tesseract remains a separate installation. Non-standard installations must be on PATH or configured explicitly; SnapSeek does not fall back to another OCR engine.

### D-022 — Use the approved logo and vector icons for desktop branding
- **Status:** Accepted by user for the Phase 6 branding update
- **Context:** The desktop UI needs consistent product identity while remaining crisp across display sizes and source/bundled launches.
- **Decision:** Display `assets/logo/snapseek-icon.png` beside a Qt-rendered two-color `SnapSeek` wordmark, use a multi-size ICO for the native window icon, and use separate SVG navigation icons. Resolve assets relative to the source project or PyInstaller bundle root.
- **Rationale:** Raster logo artwork is appropriate for the approved icon, while actual text and vector navigation marks remain sharp at different scales.
- **Consequences:** PyInstaller builds must include `assets/logo` and `assets/icons` as data. Navigation, Stats, search, motion-setting, and window-control glyphs use SVGs from the same icon folder; no runtime icon or styling dependency is added.

### D-023 — Set a stable Windows taskbar application identity
- **Status:** Accepted for the Windows desktop integration fix
- **Context:** Windows taskbar grouping and icon association use the process AppUserModelID separately from the window icon.
- **Decision:** Set `SnapSeek.Desktop` through the Windows Shell API before creating `QApplication`, and assign the approved multi-size icon to both the application and main window.
- **Rationale:** Early process identity plus both Qt icon assignments gives Windows the metadata needed to associate the window with the intended application icon.
- **Consequences:** The AppUserModelID call is Windows-only. A notification-area/system-tray icon is not added.

### D-024 — Evaluate retrieval with a synthetic labeled screenshot set
- **Status:** Accepted by user for Phase 7
- **Context:** Keyword, TF-IDF, and semantic retrieval need a shared, repeatable relevance benchmark without using private screenshots.
- **Decision:** Generate 16 deterministic synthetic PNG screenshots with a standard-library script and label 12 queries by stable screenshot IDs. Run all three existing search APIs over the same OCR records. Report per-query and unweighted macro Precision@5, Recall@5, Precision@10, and Recall@10. Keyword matches are ordered by screenshot ID only as a deterministic tie-break; preserve TF-IDF and semantic ranking order. Keep generated PNGs and evaluation results local and ignored by Git.
- **Rationale:** Synthetic fixtures are safe to share and deterministic across environments; an explicit adapter makes the keyword function's lack of ranking scores visible without changing its API.
- **Alternatives considered:** Evaluating private screenshot collections; adding scoring behavior to keyword search; checking generated binary images into the repository.
- **Consequences:** The live evaluation depends on Tesseract, the declared scikit-learn and FastEmbed packages, and the configured semantic model. Unit tests use mock OCR records and a fake encoder. Synthetic results are not evidence of performance on personal screenshot collections.

### D-025 — Use a native frosted surface with a conventional desktop interaction model
- **Status:** Superseded by D-026 after live Windows visual verification showed the requested DWM system backdrop left the Qt client surface uniformly gray.
- **Context:** The desktop UI should gain a restrained translucent blue surface while retaining familiar window controls and remaining usable where Windows backdrop effects are unavailable.
- **Decision:** Use a frameless Qt main window with branded minimize, maximize/restore, and close controls. Start native window movement through Qt's system move operation and use Qt's `QWindow.startSystemResize()` for edge and corner resizing. On Windows 11 build 22621 or later, request the DWM transient system backdrop (Desktop Acrylic) and rounded corners. Use a solid icy-blue surface if the Windows API is unsupported or rejects the request. Keep search-bar motion short, use an in-session Reduce motion option, and keep scan statistics and OCR records in memory only.
- **Rationale:** Qt requires a frameless top-level window for per-pixel translucency on Windows. Qt system movement and resize preserve normal move, snap, resize, maximize, minimize, restore, and close interactions without changing the scanning or retrieval architecture.
- **Alternatives considered:** Keep the standard frame and use only an opaque theme; add a third-party blur dependency; build a persistent statistics or OCR store.
- **Consequences:** Windows-specific DWM calls remain isolated and optional. The opaque theme is the fallback on older Windows builds and other platforms. Native backdrop appearance still needs manual verification on a real desktop because headless tests cannot show DWM composition. No scan data or statistics are saved across app restarts.

### D-026 — Use guarded Windows Acrylic composition for the Qt client surface
- **Status:** Implemented as a guarded UI refinement; the choice of Windows composition API and its native appearance remain subject to review/manual verification.
- **Context:** `DWMWA_SYSTEMBACKDROP_TYPE=DWMSBT_TRANSIENTWINDOW` returned success and read back as type 3 on Windows 11 build 22631, but the Qt client still rendered as a uniform gray surface. Qt's transparent widget painting and DWM's system backdrop did not produce a visible blur in the live application.
- **Decision:** Request Acrylic blur through `user32.SetWindowCompositionAttribute` with `ACCENT_ENABLE_ACRYLICBLURBEHIND` on Windows build 17763 or later when Windows transparency effects are enabled and High Contrast is off. Use the icy-blue `0xFDF0E2` tint with alpha `0x26` (about 15%). With Acrylic active, use translucent Qt title and raised surfaces at alpha 64/255 (25% surface opacity); navigation and secondary controls use translucent interaction states. Keep the top-level Qt surface per-pixel transparent only while the native composition request returns success. If the platform/build or API is unavailable, transparency is disabled, or Windows rejects the request, clear translucency and use an opaque `#e8f2fc` surface. Continue to request DWM rounded corners separately.
- **Rationale:** The AccentPolicy path applies blur to the client surface shown by this Qt frameless window, whereas the previous DWM request had no visible effect here. Lowering the tint from 25% to 15% and making title/card/search surfaces 75% transparent exposes more backdrop without changing hue or blur request. Dynamic API lookup and the opaque fallback allow the app to start if the Windows API is unavailable.
- **Alternatives considered:** Keep the official DWM system-backdrop request despite the observed gray result; add a third-party blur package; capture and blur the desktop inside the app.
- **Consequences:** Microsoft documents `SetWindowCompositionAttribute` but recommends using `DwmSetWindowAttribute` instead; the specific AccentPolicy Acrylic state is not a stable public contract. Its appearance may change or become unavailable on future Windows releases. The function's Boolean return indicates whether the request was accepted, not whether blur is visibly rendered, and the API does not expose a blur-radius control. Lower-opacity Qt surfaces should reveal more of the active blur, but this latest style adjustment has not been visually verified against the reference. The documented DWM system-backdrop path was previously tested with this Qt client and produced a uniform gray surface; changing to it again is not justified without fixing that integration. Runtime request failure falls back to solid icy blue. No dependency, screenshot capture, OCR change, or search behavior change is introduced.

### D-027 — Enable native resizing on the translucent frameless window
- **Status:** Implemented after live Windows resize testing
- **Context:** Returning correct `WM_NCHITTEST` edge values did not resize the running window. Inspection showed the HWND lacked `WS_THICKFRAME`, and mouse drags still passed through at the alpha-zero edge of the layered Qt surface.
- **Decision:** Add `WS_THICKFRAME` to the Windows HWND and refresh its frame with `SWP_FRAMECHANGED`. Continue returning DPI-scaled native edge/corner hit-test results. Paint an 8-pixel icy-blue rim at very low nonzero alpha around the Acrylic client so Windows receives mouse input in the resize strip; keep the opaque fallback unchanged.
- **Rationale:** Windows' native resize loop requires the sizing frame style, and layered-window hit testing passes input through fully transparent pixels. The low-alpha rim preserves the translucent look while making the existing resize zone interactive.
- **Consequences:** Windows-specific style calls remain isolated and defensive. Automated tests verify style update/rollback and edge hit-test mapping; actual pointer resizing still requires manual confirmation on Windows at different display scales.

### D-028 — Package the Windows desktop app as a PyInstaller onedir bundle
- **Status:** Accepted by user for Phase 1 Windows packaging
- **Context:** The desktop app needs a reproducible Windows build before installer work begins.
- **Decision:** Use `app.py` as the entry point and a PyInstaller spec to build a 64-bit Windows onedir application named `SnapSeek`. Include only the desktop's SVG/PNG/ICO resources and rely on PyInstaller's standard PySide6 hooks for Qt runtime files. Keep Tesseract external and allow FastEmbed to download/cache its existing model on first use; do not bundle either.
- **Rationale:** Onedir keeps the Qt runtime and resources inspectable and avoids introducing a new packaging dependency or installer before the standalone build is verified.
- **Alternatives considered:** One-file packaging, bundling Tesseract or the model, adding an installer now.
- **Consequences:** Build using a 64-bit Windows Python environment and distribute the complete `dist/SnapSeek/` directory. The executable, Qt plugins, runtime dependencies, and asset loading must be validated before marking this packaging phase complete. Installer, shortcuts, uninstaller, signing, and GitHub Releases remain deferred.

### D-029 — Activate the frameless app on direct mouse input
- **Status:** Implemented and verified on Windows
- **Context:** The frameless Windows window did not reliably come to the foreground when clicked.
- **Decision:** Return `MA_ACTIVATE` for `WM_MOUSEACTIVATE` and, on a mouse press received by a widget owned by an inactive SnapSeek main window, make one Qt `raise_()` and `activateWindow()` request.
- **Rationale:** Direct user input is an appropriate activation trigger and covers custom title-bar controls and child widgets without focus-stealing timers or explicit foreground APIs.
- **Alternatives considered:** `WindowStaysOnTopHint`, repeated activation requests, and direct `SetForegroundWindow` calls.
- **Consequences:** Active-window clicks do not issue additional activation requests. Minimize, maximize/restore, and native resize behavior remain unchanged. The packaged executable was verified with an actual mouse click while another temporary window was foreground.

### D-030 — Use a versioned local SQLite index for unified search
- **Status:** Accepted for Phase 8
- **Context:** Repeated disk traversal on every keystroke would be slow and would not support reliable incremental updates.
- **Decision:** Store canonical local metadata in `%LOCALAPPDATA%\SnapSeek\index.sqlite3` with schema versioning, roots, item metadata, OCR text, and a reserved embedding column.
- **Rationale:** SQLite ships with Python, supports transactions and indexes, and avoids adding a local service or dependency.
- **Consequences:** The index is rebuildable. Original files are never copied, and common build/cache/environment directories are excluded by default.

### D-031 — Keep unified category scores separate from screenshot semantic scores
- **Status:** Accepted for Phase 8
- **Context:** Filename/path matching and semantic cosine similarity have different meanings and score ranges.
- **Decision:** Rank files, folders, and applications with exact/prefix/substring/path/fuzzy matching. Keep screenshot semantic ranking in its existing engine and combine categories by explicit category grouping rather than raw score comparison.
- **Rationale:** This avoids misleading cross-engine score comparisons while preserving existing screenshot relevance.
- **Consequences:** The All view is category-aware; calibrated cross-category ranking remains future work.

### D-032 — Use RegisterHotKey as an optional Windows launcher shortcut
- **Status:** Accepted for Phase 8
- **Context:** The launcher needs a global shortcut without a third-party hook dependency.
- **Decision:** Register `Ctrl+Alt+Space` by default through Win32 `RegisterHotKey`; allow `SNAPSEEK_GLOBAL_HOTKEY` to configure the combination and continue normally if registration fails.
- **Rationale:** The OS-managed shortcut is local, lightweight, and conflict-safe.
- **Consequences:** Shortcut behavior is Windows-only and requires manual validation on the target machine.

### D-033 — Isolate Windows-native window integration
- **Status:** Accepted and implemented for Stage 1
- **Context:** The desktop UI previously mixed Qt composition with ctypes declarations,
  DWM/Acrylic setup, activation, mutex, and native hit testing.
- **Decision:** Keep those responsibilities in
  `src/snapseek/platform/windows_window.py`. `desktop.py` delegates native messages
  and retains compatibility wrappers for existing private imports. Keep global
  shortcut registration in `windows_hotkey.py`.
- **Rationale:** This preserves behavior while making platform APIs independently
  testable and keeping the Qt layer focused on UI orchestration.
- **Consequences:** Platform behavior can be mocked without importing or modifying
  unrelated search/indexing code. Compatibility wrappers remain until callers migrate.

### D-034 — Isolate reusable desktop UI components
- **Status:** Accepted and implemented for Stage 2
- **Context:** `desktop.py` combined page orchestration with worker lifecycle,
  result-card rendering, grid layout, and image preview presentation.
- **Decision:** Keep workers in `src/snapseek/ui/workers.py`, result cards in
  `src/snapseek/ui/result_cards.py`, the responsive grid in
  `src/snapseek/ui/result_grid.py`, and the preview dialog in
  `src/snapseek/ui/dialogs.py`. Preserve compatibility imports from `desktop.py`.
- **Rationale:** These components have clear Qt ownership boundaries and can be
  tested without importing the full main window.
- **Consequences:** `desktop.py` remains the page/controller composition layer;
  extracted components retain existing signals, cancellation, deferred deletion,
  and error propagation behavior.

### D-035 — Isolate desktop operation orchestration
- **Status:** Accepted and implemented for Stage 3
- **Context:** `MainWindow` still constructed scan/search closures, restored cached
  snapshots, persisted scan results, and owned worker lifecycle state.
- **Decision:** Keep those responsibilities in `src/snapseek/ui/operations.py`.
  The module composes existing workflow/index/search functions through injection;
  `MainWindow` retains UI state updates and compatibility access to the active
  worker.
- **Rationale:** This reduces controller coupling without changing search,
  indexing, OCR, persistence, or cancellation semantics.
- **Consequences:** Operation helpers can be tested without constructing the full
  window. The remaining controller still handles user-facing outcome messages
  and page transitions.

### D-036 — Isolate desktop page and presentation construction
- **Status:** Accepted and implemented for Stage 4
- **Context:** `MainWindow` still contained all page widget construction, title-bar
  controls, shared visual helpers, and the complete stylesheet.
- **Decision:** Keep shared visual behavior in `src/snapseek/ui/theme.py`, page
  classes in `src/snapseek/ui/pages.py`, and title-bar/navigation presentation
  in `src/snapseek/ui/navigation.py`. `MainWindow` composes these widgets and
  retains compatibility imports.
- **Rationale:** These are presentation boundaries with stable Qt signal and
  parent-child ownership contracts; extracting them does not move application
  services or alter search/indexing behavior.
- **Consequences:** Page and navigation construction can be tested independently.
  Application-level navigation, lifecycle, native events, and user-facing
  outcome handling remain in `MainWindow`.

### D-037 — Remove platform compatibility wrappers from desktop.py
- **Status:** Accepted and implemented for Stage 5
- **Context:** `desktop.py` contained 11 compatibility wrapper functions and a
  `_platform_compatibility_context()` monkeypatch context manager that existed
  solely to make `test_desktop.py`'s `patch("snapseek.desktop.*")` calls work.
  These wrappers added indirection between `MainWindow` and the platform layer
  and forced tests to couple to `desktop.py` internals.
- **Decision:** Remove all 11 wrappers and the context manager. `desktop.py`
  imports `from snapseek.platform import windows_window` and calls platform
  functions directly. Update `test_desktop.py` to patch
  `snapseek.platform.windows_window.*` instead.
- **Rationale:** Direct calls make the platform dependency explicit, eliminate
  the monkeypatch hack, and decouple tests from `desktop.py` internals.
- **Consequences:** `desktop.py` reduced from 679 to 564 lines. All 146 tests
  pass. No changes to platform behavior, search, OCR, or indexing.

### D-038 — Harden application shutdown with deferred-close and centralized worker guard
- **Status:** Accepted and implemented for Stage 6
- **Context:** `closeEvent` previously called `self._index.close()` and `event.accept()`
  unconditionally. The `LocalIndex` RLock already serialized database access, but the
  worker thread could still be executing non-database work (OCR, model inference) when
  Qt tore down the process.
- **Decision:** Make `_start_worker` the single centralized guard: it returns `False`
  immediately when `_closing` is `True`, so no new operations can start once shutdown
  begins. In `closeEvent`, after requesting cooperative cancellation, call
  `QThreadPool.globalInstance().waitForDone(2000)` when a worker is active. If
  `waitForDone` returns `True` the worker finished within 2 seconds and the event is
  accepted normally. If it returns `False` (timeout), call `event.ignore()` to keep the
  window alive and the database open, then return without finalizing. When the worker
  eventually finishes, `_worker_completed` detects `_closing=True` and calls
  `self.close()` for a second attempt; this time no worker is running so the finalization
  path proceeds. The database and hotkey cleanup happen only in the accepted-close path.
- **Rationale:** The RLock in `LocalIndex.close()` already ensured the connection was not
  closed while `index_folder` held the lock. The new design extends that guarantee to
  non-database work and makes it impossible to close the database or exit while any
  worker thread is executing. `event.ignore()` is the standard Qt mechanism for deferring
  a close request; it keeps the window alive without adding timers or polling.
- **Alternatives considered:** Forcibly terminating the worker (rejected — damages
  in-progress writes), a longer fixed-wait with no retry (rejected — blocks the GUI
  thread indefinitely if the worker hangs), a `QTimer`-based polling retry
  (rejected — adds complexity; the worker's own completion signal is a cleaner trigger).
- **Consequences:** The close button may appear unresponsive for up to 2 seconds if a
  long-running OCR or model-inference call is active. If it exceeds 2 seconds, the
  window stays open until the worker finishes. No forcible termination; no data loss;
  no database use after close. `_start_worker` is the sole shutdown gate — explicit
  `_closing` guards in `scan_directory` and `submit_search` were not added because they
  both delegate to `_start_worker`.

### D-039 — Use watchdog with queue-based GUI marshalling for filesystem monitoring
- **Status:** Accepted and implemented for Stage 7
- **Context:** SnapSeek needed automatic index updates when files change in indexed folders, without polling or full rescans.
- **Decision:** Use `watchdog>=6.0,<7` (already in `.venv`, now declared in `pyproject.toml`). On Windows, `Observer()` auto-resolves to `WindowsApiObserver` backed by `ReadDirectoryChangesW`. Events are marshalled from the watchdog daemon thread to the GUI thread via a `queue.SimpleQueue` drained by a 50ms `QTimer`; a 250ms single-shot debounce timer batches rapid events into one `changes_ready(frozenset[ChangeEvent])` signal. `FolderMonitor` lives in `src/snapseek/filesystem_monitor.py`. `_apply_change_event` in `operations.py` maps each event kind to a targeted `LocalIndex` operation (no full rescan). Four new `LocalIndex` methods handle single-item mutations: `index_single_file`, `index_single_directory`, `remove_item`, `remove_items_under`. Monitor starts after a successful scan via `QTimer.singleShot(0)` to defer observer startup out of `_worker_completed`. `scan_directory` and `submit_search` cancel any active watch worker via `WorkerController.cancel_watch()` so user operations are never silently dropped. `stability_check=False` is used in `_apply_change_event` because the filesystem event itself confirms the change.
- **Rationale:** `watchdog` uses the native Windows API, requires no polling, and its queue+timer marshalling pattern avoids any direct Qt calls from non-GUI threads. Targeted indexing avoids unnecessary OCR re-runs and full-tree scans. Watch worker preemption keeps the UI responsive to explicit user requests.
- **Alternatives considered:** Polling via a QTimer + `os.walk` diff (too slow and CPU-intensive); `ReadDirectoryChangesW` directly via ctypes (~300 lines, same result as watchdog); always running a full `index_folder` on any event (causes OCR re-runs and blocks the worker slot).
- **Consequences:** `watchdog` is now an explicit declared dependency. Watch workers run at background priority and are preemptible by scan/search. Worker completion tokens prevent a cancelled watch worker from publishing a stale completion after a replacement operation starts, and the active slot is cleared before a valid completion is published. Reconciliation after missed events (e.g. application restart, disconnected network share) is handled by the manual "Scan / Rescan" button. Continuous monitoring and `index_applications` refresh on directory events are not yet implemented.

### D-040 — Require stable metadata and bounded retries before monitor indexing
- **Status:** Accepted and implemented for Stage 7A
- **Context:** Watchdog create, modify, and rename-destination events can arrive while a file is still being copied, written, or temporarily locked. Indexing such content could persist incomplete OCR or embeddings, while deleting an existing record on a transient access error would reduce search accuracy.
- **Decision:** `LocalIndex.index_single_file()` compares file size and `st_mtime_ns` across consecutive observations before writing an index record. Missing or changing files are deferred. `build_watch_task()` retries only the explicit transient-integrity failure with bounded cancellation-aware backoff; permanent errors are reported without indefinite retry. Rename processing indexes the destination first and removes the source record only after successful destination indexing.
- **Rationale:** Metadata stability is a small, deterministic guard against incomplete writes and preserves the existing OCR/embedding reuse path for unchanged files. Bounded retries handle short-lived Windows locks without blocking independent event paths indefinitely, and deferred failures leave valid existing records intact.
- **Consequences:** A file that remains unstable or inaccessible is reported as deferred and requires a later filesystem event or manual rescan. Automatic reconciliation, observer recovery, sleep/resume recovery, and application-shortcut refresh remain outside Stage 7A.

### D-041 — Reconcile conservatively before recovering filesystem monitoring
- **Status:** Accepted and implemented for Stage 7B
- **Context:** Watchdog can miss events after observer termination, drive removal, or Windows sleep/resume. The index must recover without launching a full scan for every event or competing with user operations.
- **Decision:** `MainWindow` owns a five-minute `QTimer` that schedules one `build_reconcile_task()` at a time through `WorkerController`. Reconciliation reuses `LocalIndex.index_folder()` metadata/OCR reuse and is deferred while scan, search, or watch work is active. `FolderMonitor` health polling reports `failed` and `root_missing` states while retaining the configured root. Recovery retries with bounded exponential backoff, reconciles a returned root first, and starts the observer only after reconciliation succeeds. Application reactivation requests the same recovery path.
- **Rationale:** A GUI-owned timer keeps scheduling visible and shutdown-safe; the existing worker lifecycle prevents overlap and stale completions. Retaining the root while paused allows disconnected drives to return without losing the recovery target, and reconciliation-before-restart avoids resuming event delivery against a stale index.
- **Consequences:** Reconciliation is intentionally not continuous per-event scanning and does not refresh Start Menu application shortcuts. Actual Windows suspend/resume, network-share, and drive-ejection behavior still require manual validation.

### D-042 — Refresh application shortcuts transactionally within configured roots
- **Status:** Accepted and implemented for Stage 7C.1
- **Context:** Start Menu shortcuts were indexed during scans, but deleted shortcuts remained searchable. Refreshing them must not remove unrelated file, folder, screenshot, or out-of-scope application records.
- **Decision:** `LocalIndex.index_applications()` discovers current `.lnk` files under the supplied roots, upserts their normalized Windows paths and metadata, and deletes stale `app` rows only when their normalized paths are within those same roots. Discovery occurs before the transaction; cancellation or any write failure rolls back the complete refresh. `build_application_refresh_task()` exposes the cancellable operation without adding monitoring or scheduling.
- **Rationale:** Root-scoped stale cleanup preserves records owned by other application roots and all non-app records. Transactional replacement prevents a cancelled or failed refresh from leaving a partially updated shortcut index.
- **Consequences:** Start Menu changes are not automatically observed yet; the existing scan path and the new task builder are the available refresh entry points until Stage 7C.2 adds dedicated monitoring.

### D-043 — Monitor Start Menu roots independently and refresh through one worker
- **Status:** Accepted and implemented for Stage 7C.2
- **Context:** Application shortcuts must update automatically without coupling Start Menu events to screenshot-folder monitoring or allowing background refreshes to compete with user scans and searches.
- **Decision:** Use a second `FolderMonitor` for configured per-user and machine-wide Start Menu roots. It schedules every available root immediately, retains unavailable roots for independent recovery, and emits the same debounced event batches to an application-specific coordinator. `MainWindow` coalesces those events into one pending application refresh and runs `build_application_refresh_task()` through the existing single-worker `WorkerController`. Refresh failures retry independently; healthy observers are not restarted because indexing failed.
- **Rationale:** A separate monitor preserves screenshot monitoring and reconciliation behavior. Full transactional shortcut refresh is safer than per-event mutations because it handles create, modify, rename, and delete uniformly while scoped cleanup protects roots that could not be enumerated.
- **Consequences:** Start Menu monitoring uses an additional watchdog observer and still requires live Windows validation, especially for permissions, root disappearance, and actual shortcut installation behavior.

### D-044 — Include Desktop shortcuts and consume restored layout once
- **Status:** Accepted and implemented
- **Context:** Apps search must discover shortcuts on both Windows Desktops while preserving the existing Start Menu monitoring and root-scoped cleanup behavior. Restoring a window after a search must not reapply the initial cached-scan landing layout.
- **Decision:** Add per-user and Public Desktop roots using `SHGetKnownFolderPath` when available, with environment fallbacks. Keep `.lnk` discovery path-based, search Start Menu roots recursively, search Desktop roots only at top level, and preserve records for unavailable or unsuccessfully enumerated roots. Track cached-scan data restoration separately from a one-shot pending landing-layout flag.
- **Consequences:** Desktop shortcut changes use the existing independent application monitor and transactional refresh worker. Redirected Desktop and real minimize/restore rendering still require manual Windows validation.

### D-045 — Extract PDF text as a standalone page-preserving component
- **Status:** Accepted and implemented for Stage 9.1
- **Context:** Document Memory Search needs local PDF text extraction without coupling the first stage to SQLite, UI, filesystem monitoring, or semantic search.
- **Decision:** Use `pypdf>=5,<6` under its BSD-3-Clause license. `pdf_extractor.py` returns immutable metadata, one-based page text records, and controlled statuses for successful, empty, OCR-required, encrypted, corrupt, unsupported, and inaccessible inputs. Cancellation raises a dedicated exception before opening or between pages.
- **Rationale:** pypdf is a lightweight pure-Python dependency compatible with the supported Windows/Python versions and does not require cloud services or native binaries. Page-level output preserves boundaries for later indexing and scanned-page OCR.
- **Consequences:** PDF indexing, PDF semantic search, password handling, page rendering, and scanned-page OCR remain future work. A document with no extractable text is reported as OCR-required, although that cannot distinguish an image-only document from an intentionally blank PDF.

### D-046 — Persist PDFs in dedicated document and page tables
- **Status:** Accepted and implemented for Stage 9.2
- **Context:** PDF text requires page boundaries, explicit extraction statuses, incremental refresh, and safe replacement without changing existing Files search behavior.
- **Decision:** Upgrade the SQLite schema to version 2 with `pdf_documents` and `pdf_pages` tables. Keep the generic `items` row as a file record, store page text separately, and use streaming SHA-256 fingerprints after metadata changes or forced integrity checks. Replace a document and its pages through one savepoint; preserve prior pages when access is transient or the source changes during extraction.
- **Rationale:** Dedicated tables avoid overloading `items.ocr_text` and preserve a future page-aware search model. Metadata-first reuse avoids hashing and extraction for unchanged files, while forced checks detect content changes that retain size and modification metadata.
- **Consequences:** PDF content search, OCR for scanned pages, passwords, and embeddings remain deferred. Existing screenshot, generic-file, application indexing, and UI behavior are unchanged.

### D-047 — Use SQLite FTS5 for local PDF keyword search
- **Status:** Accepted and implemented for Stage 9.3
- **Context:** Persisted PDF pages need responsive phrase, multi-term, and prefix keyword search without re-opening or extracting source files.
- **Decision:** Upgrade the index to schema version 3 and maintain a `pdf_pages_fts` table transactionally with `pdf_documents` and `pdf_pages`. Search uses safe tokenized plain-text queries, explainable page ranking, bounded snippets, and a three-page-per-document limit. A bounded `LIKE` fallback is used only when the runtime lacks FTS5.
- **Rationale:** FTS5 is available in the supported Python 3.13 runtime, requires no dependency, and avoids loading all page text into Python. Keeping the fallback preserves portability without changing the public search API.
- **Consequences:** PDF keyword search is available through the Documents category and All search, but semantic PDF search, OCR, passwords, and exact-page viewer navigation remain future work.

### D-048 — Render scanned PDF pages with pypdfium2 and atomically index OCR
- **Status:** Accepted and implemented for Stage 9.4
- **Context:** Image-only PDF pages need local OCR while selectable PDF text must remain authoritative.
- **Decision:** Pin `pypdfium2==5.14.0`, render one eligible page at a time at bounded resolution, pass images to the existing Tesseract adapter with a 30-second timeout, and persist extracted/OCR provenance in schema version 4. Use whole-document SHA-256 reuse and whole-document atomic replacement; transient failures preserve the previous complete index.
- **Rationale:** PDFium provides Windows x64 rendering without an additional system renderer. Atomic replacement prevents partial FTS results and generation tokens prevent stale same-path workers from recreating newer or deleted records.
- **Consequences:** Pages with meaningful selectable text are never OCRed, even when scanned imagery is also present. The packaged build includes PDFium and its wrapper/component notices; Tesseract remains an external prerequisite.

## Proposed decisions to discuss

These are not final decisions. Ask the developer before locking them in.

### P-003 — How should image identity be defined?
- **Options:** Path-based identity; a generated stable ID; content hash.
- **PRD constraint:** Use stable identifiers where practical.
- **Question:** Defer this until persistence is implemented, or choose an identity scheme earlier?

## How to maintain this file

For every meaningful technical decision:
1. Describe the problem and constraints.
2. List realistic options and their trade-offs.
3. Ask the developer for input if the choice is consequential or unclear.
4. Record the selected option and why.
5. Mark it `Accepted` only after agreement or when the PRD explicitly mandates it.
6. Update or supersede older decisions when the implementation changes.
- **Decision:** Use bounded parallel OCR (up to four workers) and persist the desktop folder plus successful OCR records in `%LOCALAPPDATA%\SnapSeek\state.json`. Records are invalidated when file size or modified time changes, and the semantic model loads lazily on first search.
- **Rationale:** Tesseract is the dominant per-image scan cost, while the local state avoids repeating OCR after a restart. The cache remains outside the repository and does not change retrieval rankings or source screenshots.
- **Alternatives considered:** Sequential OCR and in-memory-only state, which made repeated desktop scans unnecessarily slow.
