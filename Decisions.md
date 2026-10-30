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
