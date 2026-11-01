# SnapSeek — System Flow

## Scanned PDF OCR

PDF indexing extracts selectable text with `pypdf`. Whitespace-only and
control-character-only pages are treated as empty; meaningful selectable text
is retained verbatim and never sent to OCR. Empty pages are rendered one at a
time by `pypdfium2`, passed as an in-memory PIL image to Tesseract, and closed
immediately.

Rendering and OCR happen outside the SQLite write transaction. The source
fingerprint and per-path generation are revalidated before one atomic
replacement updates `pdf_documents`, `pdf_pages`, and `pdf_pages_fts`.
Transient failures preserve the prior complete document. New incomplete
documents receive `ocr_required` metadata without searchable partial pages.

> Purpose: Explain how data moves through SnapSeek and what each component is responsible for.
>
> This document describes the intended design. Sections are marked **Planned** until the behavior has actually been implemented and tested.

## 1. Product flow

The user selects a local screenshot directory in the native PySide6 desktop app, or configures `SNAPSEEK_SCREENSHOT_DIR` for the CLI. SnapSeek discovers supported image files, extracts text with OCR, and searches that text. Results point to the original image files for previews.

The current terminal query uses semantic similarity over OCR text only; it does not search filenames or image pixels. TF-IDF and keyword search remain available internally as separate retrieval functions. The terminal accepts one query and does not ask the user to choose a search mode.

## 2. High-level architecture

```text
Screenshot directory (native folder picker or CLI environment variable)
                |
                v
        Image discovery
        (src/snapseek/scanner.py)
                |
                v
       Image metadata records
                |
                v
       OCR text extraction
          (src/snapseek/ocr.py)
                |
                v
       In-memory OCR records
     (main.py / desktop_workflow.py)
                |
                v
       Semantic ranking
         (src/snapseek/semantic.py)
                |
                v
       Results: filename, image path,
       OCR text, and semantic similarity
          /                 \\
         v                   v
  CLI output (main.py)  Desktop UI (app.py)
```

**Implemented Phase 1 flow:** `main.py` reads `SNAPSEEK_SCREENSHOT_DIR` and validates that it names an existing directory. The scanner inspects direct child files only and collects metadata for supported image extensions. In the current application flow, `main.py` prints matching search results, the total discovered image count, and the OCR summary; it does not print every scanned image. If a supported file disappears before its size is read, the scanner writes a path-specific warning to standard error, skips that file, and continues. Other filesystem errors from scanning are reported by `main.py` with an actionable message and a non-zero exit status; configuration errors remain separately identified.

**Implemented Phase 2 units 1 and 2:** `src/snapseek/ocr.py` exposes `extract_text(ImageMetadata) -> str`, and `main.py` calls it for each image returned by `scan_images()`. Empty OCR output is valid and counts as success. An `ImageOCRError` produces a path-specific warning on standard error, then processing continues. A missing `pytesseract` package or unavailable/unlaunchable Tesseract executable produces one actionable setup error, stops further OCR calls, and returns a non-zero status. The summary reports discovered images, OCR successes, image failures, and images not processed. OCR text remains in memory for the current run. The user has reported successfully testing real Tesseract OCR on a screenshot; automated tests mock OCR and do not independently verify real recognition or quality.

```text
Screenshot directory
        |
        v
Discover supported image files
        |
        v
Collect filename, path, extension, size
        |
        v
Use metadata for the current run
        |
        +-- vanished file: warn and skip
        +-- other scan filesystem error: report and exit non-zero
```

OCR and the UI were deliberately absent from the original Phase 1 inventory;
they are implemented in later phases.

## 3. Module responsibilities

| Module | Responsibility | Planned phase |
|---|---|---|
| `main.py` | Existing CLI entry point: validates its query/configuration, scans, coordinates OCR, and prints semantic results and a summary | Phases 1–5 (implemented) |
| `app.py` | Starts the native PySide6 desktop application without loading the model at import time | Phase 6 (implemented) |
| `src/snapseek/desktop.py` | Composes native pages, navigation, styling, native-window adaptation, user-facing operation outcomes, reconciliation scheduling, and monitor recovery coordination | Phase 6 / Stage 4 / Stage 7B (implemented) |
| `src/snapseek/platform/windows_window.py` | Owns optional Windows-native window integration: AppUserModelID, single-instance activation, Acrylic/DWM composition, resize-frame setup, hit testing, and native message adaptation | Stage 1 (implemented) |
| `src/snapseek/ui/workers.py` | Owns `BackgroundWorker`, `WorkerSignals`, and `WorkerOutcome`; runs cancellable widget-independent tasks and emits GUI-thread outcomes | Stage 2 (implemented) |
| `src/snapseek/ui/result_cards.py` | Owns screenshot and unified file/folder/app result-card widgets and their presentation/open actions | Stage 2 (implemented) |
| `src/snapseek/ui/result_grid.py` | Owns batched result-card creation, deferred rendering, responsive layout, and preview signal forwarding | Stage 2 (implemented) |
| `src/snapseek/ui/dialogs.py` | Owns the read-only screenshot preview dialog and missing-image fallback | Stage 2 (implemented) |
| `src/snapseek/ui/operations.py` | Owns cached snapshot restore, scan/search task construction, scan persistence, active worker lifecycle coordination, targeted watch-task construction (`build_watch_task`), bounded retry/backoff for transient watch indexing failures, and `WorkerController.cancel_watch()` for watch-worker preemption | Stage 3 / Stage 7 / Stage 7A (implemented) |
| `src/snapseek/filesystem_monitor.py` | Owns independent `FolderMonitor` instances for screenshot and application roots; wraps watchdog observers, marshals events to the GUI thread via `SimpleQueue`/`QTimer`, applies debounce, reports observer health/root loss, and retains unavailable roots for recovery | Stage 7 / Stage 7B / Stage 7C.2 (implemented) |
| `src/snapseek/ui/theme.py` | Owns shared palette, asset resolution, icon scaling, shadows, search-field focus behavior, and application stylesheet generation | Stage 4 (implemented) |
| `src/snapseek/ui/pages.py` | Owns Home, Folder, Stats, and Settings page construction, page-local signals, and page presentation state | Stage 4 (implemented) |
| `src/snapseek/ui/navigation.py` | Owns the branded title bar, navigation layout, window-control presentation, and native move interaction adapter | Stage 4 (implemented) |
| `src/snapseek/desktop_workflow.py` | Coordinates selected-folder scanning, lazy model setup, per-image OCR, and semantic search for the desktop UI | Phase 6 (implemented) |
| `src/snapseek/config.py` | Read `SNAPSEEK_SCREENSHOT_DIR` and report missing or invalid directory configuration | Phase 1 (implemented) |
| `src/snapseek/scanner.py` | Discover direct-child PNG, JPG, JPEG, and WEBP files case-insensitively; collect file metadata | Phase 1 (implemented) |
| `src/snapseek/__init__.py` | Mark the SnapSeek source package | Phase 1 (implemented) |
| `src/snapseek/ocr.py` | Extracts text for one `ImageMetadata` using local Tesseract through `pytesseract`; reports setup and image-specific failures | Phase 2 (implemented) |
| `src/snapseek/search.py` | Keyword substring matching and TF-IDF ranking over supplied OCR records | Phases 3–4 (implemented) |
| `src/snapseek/semantic.py` | Load FastEmbed lazily and rank supplied OCR records with local semantic embeddings | Phase 5 (implemented) |
| `src/snapseek/pdf_extractor.py` | Extract local PDF metadata and page-level text with explicit error and OCR-required classifications | Stage 9.1 (implemented) |
| `src/snapseek/unified_index.py` | Persist generic items plus transactional PDF documents/pages with incremental fingerprints and status preservation | Stage 9.2 (implemented) |
| `src/snapseek/unified_search.py` | Rank generic unified results and expose page-level PDF keyword search | Stage 9.3 (implemented) |
| `src/snapseek/evaluation.py` | Validate stable-ID relevance labels, adapt existing result types, and calculate retrieval metrics | Phase 7 (implemented) |
| `scripts/generate_synthetic_dataset.py` | Generate the fixed OCR-readable PNG benchmark with standard-library code | Phase 7 (implemented) |
| `scripts/evaluate_retrieval.py` | OCR the complete generated dataset, invoke all three search functions, and print metrics plus runtime versions | Phase 7 (implemented) |
| `tests/test_scanner.py` | Verify configuration and inventory behavior with temporary directories and files | Phase 1 (implemented) |
| `tests/test_main.py` | Verify application OCR orchestration, output association, continuation, setup errors, and semantic CLI output | Phases 2 and 5 (implemented) |
| `tests/test_search.py` | Verify keyword behavior and TF-IDF ranking, result association, ties, zero scores, and empty inputs | Phases 3 and 4 (implemented) |
| `tests/test_semantic.py` | Verify semantic scoring, record association, ties, errors, and empty inputs with fake embeddings | Phase 5 (implemented) |
| `tests/test_desktop_workflow.py` | Verify directory validation, scan/OCR failure handling, model setup, and reuse of scanned records | Phase 6 (implemented) |
| `tests/test_desktop.py` | Headless UI and worker tests using mocked OCR and fake embeddings | Phase 6 (implemented) |
| `tests/test_evaluation.py` | Verify label validation, ranking adapters, metric edge cases, and deterministic retrieval evaluation | Phase 7 (implemented) |
| `tests/test_pdf_extractor.py` | Verify page boundaries, metadata, cancellation, source integrity, and PDF failure classifications | Stage 9.1 (implemented) |

Create modules gradually. Do not add a module merely to match a diagram if its responsibility is not needed yet.

Stage 2 keeps business and platform logic out of the extracted UI components.
Workers depend on workflow/search exception contracts, cards depend on result
value objects and launch helpers, and the grid depends on the cards. `desktop.py`
continues to orchestrate pages and re-exports the extracted names for existing
imports.

Stage 3 moves scan/search orchestration and worker ownership out of `MainWindow`.
`ui/operations.py` builds injected tasks around the existing workflow, index, and
search functions; `MainWindow` remains responsible for page state, user
notifications, and applying completed outcomes. No ranking or search engine
behavior is changed.

Stage 4 moves page construction into `ui/pages.py`, title-bar and navigation
presentation into `ui/navigation.py`, and shared visual helpers/styles into
`ui/theme.py`. `MainWindow` now composes these widgets, connects their signals,
and handles application-level navigation/outcomes. The extracted modules do
not access the index, OCR, semantic ranking, or persistence services.

## 4. Screenshot record

A planned indexed record contains fields similar to:

```python
{
    "image_id": "stable-unique-id",
    "filename": "python_error.png",
    "path": "/path/to/python_error.png",
    "extension": ".png",
    "size_bytes": 145768,
    "ocr_text": "ModuleNotFoundError: No module named ...",
    "indexed_at": "ISO-8601 timestamp",
    "ocr_status": "success"
}
```

The values above are illustrative only. Actual paths and values must come from the local runtime.

Important distinctions:
- `path` points to the original image; image bytes are not stored in the metadata record.
- `ocr_text` contains extracted text and may be empty.
- `ocr_status` and error details are separate from the extracted text.
- A failed OCR operation for one image must not stop the entire indexing operation.
- Do not expose private paths unnecessarily in logs or public documentation.

## 5. Search flows

### Level 1 — Keyword search (implemented internal function)

1. Receive a query and supplied OCR records.
2. Reject an empty or whitespace-only query.
3. Match the case-folded query as a substring of OCR text; filenames are not searched.
4. Return matching records in input order, retaining their image metadata and OCR text.

This function remains available as a building block; the Phase 4 CLI uses TF-IDF rather than exposing a keyword/TF-IDF mode selector.

### Level 2 — TF-IDF search (implemented)

1. Use successful OCR text records collected during the current application run as the corpus.
2. Fit a default word-unigram TF-IDF vectorizer and transform the user's query.
3. Compute cosine similarity between the query vector and each document vector.
4. Exclude zero-score records; sort remaining results by descending score, then normalized image path.
5. Return scored results that retain their original image metadata and OCR text.

The vectorizer is fitted for each search run and is not cached or persisted. Empty collections, corpora without usable terms, and queries with no vocabulary overlap return no matches. Phrase queries rank by their component words rather than requiring an exact phrase.

### Level 3 — Semantic search (implemented)

1. Load FastEmbed and `sentence-transformers/all-MiniLM-L6-v2` lazily after configuration and scanning.
2. Batch-encode nonempty OCR text records and encode the query once.
3. Compare each document vector with the query vector using cosine similarity.
4. Return all nonempty OCR records ordered by descending score; break equal scores by normalized POSIX image path. No score threshold is applied.
5. The CLI reuses the encoder for its invocation. The desktop app reuses one encoder while it is running; document vectors are recomputed for each submitted query and are not persisted.

The model downloads on first use when it is not already cached. A missing FastEmbed package or model load/download failure is reported before OCR starts. OCR text, scan records, and generated vectors stay in memory; FastEmbed's downloaded public model files remain in its local cache. Semantic search covers extracted text, not objects, diagrams, or image content OCR did not capture. Phase 7 evaluates the configured model on the controlled synthetic set; those results do not measure quality on personal screenshot collections.

## 5. PDF persistence flow (implemented)

```text
PDF create/modify/reconcile event
                |
                v
       LocalIndex.index_pdf()
                |
       metadata + streaming SHA-256
                |
       unchanged? ---- yes ---> skip extraction
                |
                no
                v
       pypdf page extraction
                |
       post-read fingerprint check
                |
                v
   atomic pdf_documents + pdf_pages replacement
```

PDF metadata and page text are stored separately from `items.ocr_text`, so
Stage 9.2 does not change existing Files search behavior. Extraction statuses
are persisted explicitly; transient inaccessible or changing files are deferred
and retain their previous valid pages. Rename handling indexes the destination
before removing the source, and confirmed deletion removes generic and PDF
records together. PDF content search, scanned-page OCR, passwords, and
embeddings are future work.

## 6. PDF content search flow (implemented)

PDF searches query `pdf_pages_fts` joined to `pdf_documents`; they never open
or extract the original file. Plain user text is tokenized safely, phrase and
all-term/prefix matches are ranked by phrase presence, term coverage,
frequency, and filename/title matches, then limited to three pages per
document. A bounded `pdf_pages` SQL fallback is retained for SQLite runtimes
without FTS5. Documents results remain separate from generic Files results in
the All category, and the UI displays the page number plus an HTML-escaped
snippet with safe term highlighting.

## 7. Error and edge-case flow

- **Directory missing or not a directory:** Report a configuration error; do not scan unrelated locations.
- **Directory scan filesystem error:** Report the affected directory and advise checking that it exists and is readable; return a non-zero status.
- **Supported image vanishes during metadata collection:** Print a warning naming the path, skip that image, and continue scanning.
- **OCR wrapper unavailable:** The standalone OCR function raises `OCRDependencyError` with installation guidance.
- **Tesseract executable unavailable or unlaunchable:** The OCR function preserves a non-default `pytesseract.pytesseract.tesseract_cmd`, uses the default command through PATH, and on Windows checks `C:\Program Files\Tesseract-OCR\tesseract.exe` as a fallback. If launch still fails, it raises `TesseractExecutableError` with setup guidance. Non-standard installs must be on PATH or configured explicitly.
- **Image-specific OCR failure:** The standalone OCR function raises `ImageOCRError` naming the image; integrated `main.py` warns and continues with the next image.
- **Integrated image-specific OCR failure:** `main.py` warns to standard error and continues with later images; the final status is non-zero if any image failed.
- **Integrated OCR setup failure:** `main.py` reports the setup error once, stops OCR calls, reports unprocessed images in the summary, and returns a non-zero status.
- **TF-IDF dependency unavailable:** TF-IDF search reports an actionable scikit-learn installation error and returns a non-zero status; keyword search itself does not require scikit-learn.
- **FastEmbed unavailable:** Semantic setup reports an actionable dependency error before OCR starts and returns a non-zero status.
- **Semantic model unavailable:** A first-download or local model-load failure is reported before OCR starts; the application does not silently fall back to TF-IDF.
- **Semantic inference failure:** Report a semantic search error and return a non-zero status after OCR; unrelated programming errors are not intentionally converted into semantic errors.
- **Desktop worker failure:** Show a readable status message in the window; expected OCR/model errors retain their specific category. Unexpected exceptions are logged with details rather than silently discarded.
- **Directory selection changed:** Discard the old scan and search results. The user must explicitly scan the new folder; the process-local encoder can be reused.
- **Desktop window closes during work:** `_start_worker` rejects new operations immediately when `_closing` is `True`. `closeEvent` requests cooperative cancellation via the cancel event, then calls `QThreadPool.globalInstance().waitForDone(2000)`. If the worker finishes within 2 seconds, the event is accepted, the database is closed, and Qt shuts down. If the worker is still running after 2 seconds, `event.ignore()` keeps the window and database alive; when the worker finishes, `_worker_completed` sees `_closing=True` and calls `self.close()` for a second attempt, which then takes the finalization path. The database is never closed while a worker thread is executing.
- **Monitor watch worker vs. user operation:** `scan_directory` and `submit_search` cancel any active "watch" background worker via `WorkerController.cancel_watch()` before starting their own task. Watch workers are low-priority index maintenance; they must yield to explicit user requests. `cancel_watch()` clears `active_worker` synchronously so the new task slot is available immediately.
- **Desktop translucency unavailable or disabled:** Use the opaque icy-blue theme. The Windows Acrylic request is isolated behind a Windows/build and system-preference check and falls back if the composition API is unavailable or rejects it. Headless tests cannot verify visible blur; the live material still needs a manual Windows desktop check.
- **Unsupported extension:** Ignore the file.
- **Unreadable/corrupt image:** Record or report the failure and continue with other images.
- **No OCR text:** Keep the image record; represent empty OCR output safely.
- **Empty library:** Show a clear message rather than failing.
- **Empty query:** Do not run a meaningless search; explain what the user can enter.
- **No matches:** Return an empty result set and show a useful message.
- **Source image moved or removed:** Detect an invalid path when practical and avoid pretending the result is still available.
- **Duplicate filenames:** Distinguish files using their paths and, where needed, a stable identifier.

## 7. Evaluation flow (implemented)

```text
Fixed synthetic screenshot script + stable-ID labels
                         |
               scan and OCR every image
                         |
         +---------------+----------------+
         |               |                |
         v               v                v
      Keyword          TF-IDF          Semantic
       search           search          search
         +---------------+----------------+
                         |
                         v
         Precision/Recall at K=5 and K=10
                         |
                         v
          Per-query rows and macro averages
```

The checked-in `evaluation/queries.json` labels 12 queries against 16 synthetic screenshots. `scripts/generate_synthetic_dataset.py` writes deterministic PNGs locally to the ignored `evaluation/generated/` directory. The evaluator verifies the complete expected ID set, rejects malformed or unknown labels, and stops without partial metrics if OCR or semantic setup fails. The tests use supplied OCR records and a fake encoder, so they require neither Tesseract nor a model download.

All three existing search functions receive the same records for every query. Keyword results have no relevance scores; the evaluation sorts matching IDs only to break ties deterministically. This ordering is not a relevance ranking. TF-IDF and semantic result orders are preserved. Duplicate IDs count once at their first rank. Precision@K divides by K even when fewer results are returned; recall divides by the count of labeled relevant IDs. Empty relevance labels are invalid. Report per-query values and unweighted macro-averages, along with Python, OCR, search dependency, and model versions. The benchmark is synthetic and does not establish performance on personal screenshot collections.

## 8. Development sequence and gates

1. **Phase 1 — Inventory (implemented):** read the directory from `SNAPSEEK_SCREENSHOT_DIR`, scan direct children for supported extensions, collect metadata, print inventory and total count, and test with `unittest` temporary directories.
   - **Gate:** unit tests cover supported and unsupported files, case differences, sorting, empty and missing paths, ignored subfolders, a simulated disappearing image, and a simulated directory listing error. Manual use against a screenshot directory is still a user-run action.
2. **Phase 2 — OCR:** configure Tesseract, extract and record text, isolate failures.
   - **Units 1 and 2 implemented:** standalone text extraction and application integration, with mocked tests for output association, empty results, per-image continuation, and setup failures.
   - **Gate:** the user reports a successful real Tesseract run on a screenshot. Unit tests do not establish general Tesseract recognition quality.
3. **Phase 3 — Keyword search (implemented):** retain the internal keyword function for case-insensitive OCR-text substring matching.
4. **Phase 4 — TF-IDF (implemented):** the same positional CLI query uses TF-IDF ranking internally, without a manual mode selector. Keyword search remains a separate tested function for future hybrid retrieval. Scores are descending, zero-score records are excluded, and ties use normalized image paths. No persistence, cache, hybrid ranking, or semantic search is included.
5. **Phase 5 — Semantic search (implemented):** FastEmbed embeddings and cosine ranking are the default behind the existing single query; TF-IDF and keyword functions remain available internally. The model and vectors are reused only during the current run; there is no persistent cache or threshold.
6. **Phase 6 - Native desktop UI (implemented):** `app.py` starts the PySide6 app; `main.py` remains the CLI. Settings owns the selected screenshot folder, browse control, scan/rescan action, progress, and status. The navigation contains Home, Stats, and Settings. Work runs on Qt's thread pool, OCR uses bounded parallel Tesseract workers, and one natural-language query uses semantic search. The UI shows image previews, scores, OCR snippets, scan statistics, and error states. Branding uses the approved logo PNG/ICO plus separate SVG navigation assets, resolved from the project root or PyInstaller bundle root. The Windows process AppUserModelID is set before `QApplication`, and the logo icon is assigned to both the application and main window. The selected desktop folder and successful OCR text persist in `%LOCALAPPDATA%\SnapSeek\state.json`; file size and modified time invalidate stale records. The semantic encoder is loaded lazily on the first search and reused while the app process runs.
7. **Phase 7 — Evaluation and documentation (implemented):** deterministic synthetic screenshots and labels, metrics for keyword/TF-IDF/semantic approaches, setup guide, and repository hygiene.

**Desktop UI/UX polish (implemented):** The Qt main window uses a branded frameless title area with SVG minimize, maximize/restore, and close controls. Windows receives DPI-scaled `WM_NCHITTEST` edge/corner results and the HWND has `WS_THICKFRAME` refreshed with `SWP_FRAMECHANGED`, allowing the OS resize loop to handle all edges and corners. Because the Acrylic client is per-pixel layered, an 8-pixel very-low-alpha icy rim keeps the resize strip from passing mouse input through. A click on a SnapSeek widget while the main window is inactive makes one Qt raise/activation request; Windows `WM_MOUSEACTIVATE` also returns `MA_ACTIVATE`. This is input-triggered, does not set an always-on-top style, and is covered by native-message and Qt event-filter tests. Qt system move remains attached to the open title-bar area, and title buttons do not start dragging. On supported Windows builds, SnapSeek requests Acrylic blur through `SetWindowCompositionAttribute` with an icy-blue tint (`0xFDF0E2`) at about 15% alpha (`0x26`) and uses DWM for rounded corners. The title bar and raised cards/search surfaces use alpha 64/255 (25% surface opacity); navigation and secondary controls use translucent states while Acrylic is active. The main client stays transparent. When composition is unavailable or Windows transparency effects are disabled, the app uses an opaque icy-blue theme. The reduced tint and Qt surface opacity are intended to reveal more backdrop; their match to the reference still needs visual confirmation on the user's Windows desktop. AccentPolicy does not expose blur-radius control, and a successful API return only means the request was accepted, not that the desired visual strength was achieved. Microsoft's documented DWM system-backdrop option was previously tested but produced a uniform gray Qt client; switching renderers would need a separate integration investigation. The title-control focus border is shown only for keyboard focus, while hover and press remain subtle. The search transition expands the compact search bar after a valid query is accepted, using a short height and opacity transition; Reduce motion skips it, and navigating away or enabling Reduce motion finishes an active transition cleanly. Stats cards use local SVG icons and show an indeterminate activity bar during scans because the workflow has no fractional progress reporting. Statistics reflect the restored or current scan, including setup failure and partial OCR failure states. The app restores the last successful desktop OCR scan after restart; selecting Scan / Rescan explicitly refreshes new or changed files.

Final desktop QA, including the Windows backdrop and window-interaction check, remains a separate manual task. Phase 8 adds a local Windows onedir build without changing the app's scan, OCR, or search flow.

## 9. Keep this file current

After each meaningful implementation:
- Update the flow to match actual behavior, not just the intended design.
- Label unimplemented features as `Planned`.
- Note important error paths and caching behavior.
- Keep architecture changes consistent with `Decisions.md`.

## Phase 3–6 implementation status (current)

The terminal flow accepts one positional query and reads its directory from `SNAPSEEK_SCREENSHOT_DIR`. `main.py` validates the query and configuration, scans once, loads the semantic model before OCR when images were found, calls OCR once per image until a setup error occurs, then passes successful records to `search_semantic()`. Image-specific OCR errors warn and continue; missing OCR setup reports one error and stops further OCR calls. The CLI prints an OCR summary and ranked results without rescanning or rerunning OCR during search.

`search_keyword()` keeps its Phase 3 case-insensitive substring behavior, and `search_tfidf()` keeps its Phase 4 ranking behavior. The Phase 5 CLI and Phase 6 desktop UI use `search_semantic()` with existing OCR records. Results preserve their source metadata/text and are sorted by descending cosine similarity, then normalized path. The desktop encoder is reused while the app runs; each search computes vectors in memory, without persisting OCR text, scan metadata, or embeddings. A blank query is rejected, and no search-mode selector is exposed.

The native UI is launched with `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe app.py`. The Settings page provides the native picker and explicit scan/rescan action; `SNAPSEEK_SCREENSHOT_DIR` supplies the initial folder when set. The existing CLI remains `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe main.py "query"` and uses the environment variable for its directory.

Desktop tests run headlessly with `$env:QT_QPA_PLATFORM = "offscreen"; $env:PYTHONPATH = "src"; $env:OPENBLAS_NUM_THREADS = "1"; $env:OMP_NUM_THREADS = "1"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`. They mock OCR and use fake embeddings, so they do not validate real semantic search through the UI. Desktop regression coverage also checks title-control states, resize-edge mapping, navigation state, search animation and Reduce motion, and stats states. Headless tests do not validate real Windows DWM composition, native resizing/dragging, or visual contrast on a live desktop. The separate Phase 7 synthetic evaluation uses real OCR and the configured model; its commands, versions, and limits are documented in `README.md` and Section 7.

## Phase 8 — Local Windows onedir packaging

`SnapSeek.spec` packages `app.py` as a Windows x64 PyInstaller onedir application named `SnapSeek`. The spec includes the runtime `assets/icons` SVGs and the logo PNG/ICO under matching `assets/...` destinations; `resolve_asset_path()` uses PyInstaller's `_MEIPASS` root when present and the project root for source launches. PyInstaller's PySide6 hooks collect Qt runtime DLLs and plugins; the build output must be inspected for the `qwindows` platform plugin, SVG support, and ONNX Runtime binaries. FastEmbed is imported dynamically by `semantic.py`, so it is declared as a hidden import. Dependency test-data directories and NumPy's unused `_multiarray_tests` extension are excluded from the collected output. The semantic model and Tesseract remain external and are not bundled.

Build from the repository root in an activated 64-bit Windows Python environment using the project `.venv`:

```powershell
$env:OPENBLAS_NUM_THREADS = "1"
$env:OMP_NUM_THREADS = "1"
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm SnapSeek.spec
```

The executable is `dist\SnapSeek\SnapSeek.exe`; run it directly and keep the whole `dist\SnapSeek\` directory together. The local Windows 11 x64 build and direct startup have been verified. The bundled window assets, Qt Windows/SVG plugins, and ONNX Runtime binaries were inspected. A separate visible-window mouse test confirmed that the packaged executable becomes the foreground window when clicked while another temporary test window is active. The GUI can launch without `SNAPSEEK_SCREENSHOT_DIR`; users can enter, browse, and scan a directory from Settings. The model cache was not present during the local smoke test, so scan/search was not run to avoid starting a download. This does not yet verify a clean-machine build, model bootstrap, OCR in the packaged process, or installer behavior.

This phase produces only a standalone app directory. Tesseract must be installed separately, and FastEmbed downloads `sentence-transformers/all-MiniLM-L6-v2` on first use when no local cache is available. Installer, shortcuts, uninstaller, signing, release workflow, and GitHub Releases are deferred.

## Phase 8 — Unified Windows search (implemented core; monitoring planned)

The Home page keeps the existing frosted-glass search bars and adds a
Screenshots / Files / Apps / All toggle below each bar. The selection is
persisted in the local state file and updates the placeholder. Screenshot
queries continue through the existing OCR and semantic engine. File and app
queries use the SQLite index and never invoke OCR or embeddings. All combines
semantic screenshot results with separately ranked file and app results; raw
scores are not compared across engines.

`src/snapseek/unified_index.py` owns schema version 1 and transactional,
root-scoped application shortcut refresh:

```text
schema_version(version)
roots(path, kind, indexed_at, status, error)
items(path, item_type, name, extension, parent_path, size_bytes,
      modified_ns, launch_target, ocr_text, embedding, indexed_at)
```

The selected folder is indexed recursively in a Qt background worker. Canonical
paths prevent duplicates across overlapping roots. New and modified files are
updated, deleted descendants are removed, and common `.git`, `.venv`,
`__pycache__`, build, dist, node_modules, and cache directories are skipped.
Start Menu `.lnk` entries are indexed recursively; per-user and Public Desktop
top-level `.lnk` entries are indexed as applications and opened through
Windows defaults. Desktop roots prefer Windows Known Folder resolution and
fall back to environment paths. Configured roots
remain distinct from available roots, and stale cleanup only uses roots that
were enumerated successfully. The reserved embedding column documents the persistence
boundary; the current desktop path reuses its existing in-memory encoder and
per-file embedding cache, but does not yet persist vectors across restarts.

Continuous screenshot and application filesystem watchers are installed
independently, with debounced events, serialized refresh workers, reconciliation,
and bounded observer recovery. The optional `RegisterHotKey` launcher defaults to
`Ctrl+Alt+Space` and restores/focuses the existing window; conflicts are
reported through logging and do not block startup.

## Stage 1 — Windows window platform ownership (implemented)

`src/snapseek/platform/windows_window.py` is the single owner of ctypes
structures and Windows-native window behavior. It provides defensive helpers
for AppUserModelID setup, single-instance mutex activation, Acrylic and DWM
composition, native resize-frame setup, foreground activation, DPI-aware
resize hit testing, and native message interpretation. `desktop.py` remains
the Qt composition/orchestration layer: its `nativeEvent()` delegates to the
platform adapter via `from snapseek.platform import windows_window` and calls
`windows_window.*` functions directly. `windows_hotkey.py` remains the owner
of `RegisterHotKey`, registration, and unregistration; the platform module
imports its `WM_HOTKEY` constant rather than duplicating shortcut functionality.
Non-Windows imports and fallbacks remain safe.

## Stage 5 — Remove platform compatibility layer (implemented)

Removed 11 compatibility wrapper functions and the `_platform_compatibility_context()`
monkeypatch context manager from `desktop.py`. `desktop.py` now imports
`from snapseek.platform import windows_window` and calls platform functions
directly (e.g., `windows_window.enable_acrylic_blur(hwnd)`). Test patches target
`snapseek.platform.windows_window.*` instead of `snapseek.desktop.*`.
`desktop.py` is now 564 lines (down from 679).
