# SnapSeek

SnapSeek searches local screenshot text. It scans image files, extracts visible text with Tesseract OCR, and ranks the resulting records with local semantic embeddings. The repository also includes separate keyword and TF-IDF search functions and a synthetic retrieval evaluation.

The desktop launcher also searches indexed files, folders, Windows Start Menu
applications, and persisted PDF pages. The unified index and metadata remain local in
`%LOCALAPPDATA%\SnapSeek\index.sqlite3`; original files and screenshots are
never copied or modified.

## Requirements

- Python 3.11, 3.12, or 3.13
- Tesseract OCR installed separately and available on `PATH`, configured through `pytesseract.pytesseract.tesseract_cmd`, or installed in the standard Windows location `C:\Program Files\Tesseract-OCR\tesseract.exe`
- Internet access for the first FastEmbed model download; later runs use the local model cache
- Windows is the primary desktop target. The core scanner and retrieval modules are Python modules.

## Install

From the repository root in PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Use `py -3.11` or `py -3.12` if that is the interpreter you want to run. The package dependencies include `pytesseract`, scikit-learn, FastEmbed, and PySide6. Tesseract itself is a separate system installation. The first semantic search may download `sentence-transformers/all-MiniLM-L6-v2` into FastEmbed's local cache.

## Run

Start the desktop application:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe app.py
```

SnapSeek uses a Windows single-instance guard. Launching it again restores and
activates the existing window instead of opening a second copy.

The Home search field defaults to **All** and has a category toggle for
Screenshots, Files, Documents, Apps, and All. Scan a selected folder from Settings to
index its files incrementally. The global launcher shortcut defaults to
`Ctrl+Alt+Space`; configure it before launch with
`$env:SNAPSEEK_GLOBAL_HOTKEY = "Ctrl+Alt+Space"`. If another application owns
the shortcut, SnapSeek continues without global activation.

Or use the CLI with a local screenshot directory:

```powershell
$env:PYTHONPATH = "src"
$env:SNAPSEEK_SCREENSHOT_DIR = "C:\Path\To\Screenshots"
.\.venv\Scripts\python.exe main.py "payment failed"
```

The scanner reads supported image files directly inside the selected directory. SnapSeek does not modify source images. Desktop OCR results and the selected folder are saved in the per-user SnapSeek state under `%LOCALAPPDATA%\SnapSeek\state.json`; the cache is keyed by file size and modified time. Rescanning reprocesses new or changed files.

## Desktop appearance and session data

The desktop app uses a restrained icy-blue surface and a custom branded title bar. Drag the open title-bar area to move the window; use its Minimize, Maximize/Restore, and Close controls as usual. The window remains resizable from its edges and corners.

On supported Windows builds, SnapSeek requests a blue-tinted Acrylic blur through `SetWindowCompositionAttribute` with about 15% tint alpha and uses DWM for rounded corners. With blur active, the title bar and raised search/cards have 25% white surface opacity (75% transparent), and navigation/secondary controls use similarly translucent states. The app keeps a transparent Qt client surface only when the composition request returns success. AccentPolicy does not expose a blur-radius control, and its return value cannot confirm the rendered appearance. The documented DWM system-backdrop route was previously tested with this Qt window and showed a uniform gray client surface, so it is not currently a reliable replacement. The app respects Windows transparency and High Contrast settings; when blur is unavailable, disabled, or rejected, it uses an opaque icy-blue surface. Microsoft documents `SetWindowCompositionAttribute` but advises against using it and recommends `DwmSetWindowAttribute`; the specific AccentPolicy Acrylic state is not a stable, public contract. See [Microsoft's API note](https://learn.microsoft.com/en-us/windows/win32/dwm/setwindowcompositionattribute). Headless tests cover request/fallback decisions, not the blur itself. The latest style change exposes more native backdrop through Qt surfaces; its visual match to the reference remains unverified until checked on Windows.

The title bar, navigation, search field, Stats cards, and window controls use local SVG assets. Stats also shows an indeterminate activity bar while scanning; it does not show a made-up percentage because the scan workflow does not report fractional progress. Reduce motion skips the short search-bar transition and lasts for the current session. Scan counts, OCR records, and embeddings remain in memory only. SnapSeek does not save aggregate stats or searchable screenshot data, so each application run requires a new scan before search.

Manual Windows backdrop check: enable **Settings → Personalization → Colors → Transparency effects**, start SnapSeek, and place it over a colorful desktop or another open window. The area behind the main content should remain visible as a soft, blue-tinted blur while cards and text stay readable. Move, resize, minimize, restore, and maximize the window. Then turn Transparency effects off and restart SnapSeek; the body should use a solid icy-blue surface. Also verify legibility with Windows High Contrast enabled. A successful native API return alone does not confirm the visual effect.

## Windows standalone build

The Windows desktop build uses PyInstaller's `onedir` layout. Build on Windows 10/11 x64 with a 64-bit Python 3.11, 3.12, or 3.13 environment. From the repository root in PowerShell, install the declared packaging extra if it is not already installed, then build:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[packaging]"
$env:OPENBLAS_NUM_THREADS = "1"
$env:OMP_NUM_THREADS = "1"
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm SnapSeek.spec
```

The executable is `dist\SnapSeek\SnapSeek.exe`; keep the complete `dist\SnapSeek\` directory together because it contains the Qt runtime, Python dependencies, and UI assets. Launch it directly with:

```powershell
.\dist\SnapSeek\SnapSeek.exe
```

The spec includes the runtime SVG icons and PNG/ICO logo. PyInstaller's PySide6 hooks collect Qt runtime components; dependency test-data directories and NumPy's unused `_multiarray_tests` extension are excluded. It does not include the design-reference board, evaluation files, personal screenshots, Tesseract, or the FastEmbed model. Tesseract must be installed separately and available on `PATH`, configured through `pytesseract.pytesseract.tesseract_cmd`, or installed at `C:\Program Files\Tesseract-OCR\tesseract.exe`. If scanning reports an OCR setup error, install or configure Tesseract and restart the app.

The semantic model `sentence-transformers/all-MiniLM-L6-v2` is downloaded by FastEmbed on the first semantic search when it is not already in the local cache. Later searches use that cache. If model setup reports a download or cache error, check connectivity for the initial download or retry with a valid local cache. The desktop app does not require `SNAPSEEK_SCREENSHOT_DIR`; choose, change, and scan a folder from Settings. Desktop OCR runs use up to four concurrent Tesseract workers, and unchanged OCR results are reused on rescan and after restarting the app. Embeddings remain in memory for the current run.

Phase 8 and later use SQLite schema version 4 with `roots`, `items`,
`pdf_documents`, `pdf_pages`, and `pdf_pages_fts` tables. Items
store type, path, name, extension, parent, size, modification time, optional
launch target, OCR text, and a reserved embedding column. Indexing is
incremental, removes deleted descendants, deduplicates canonical paths across
overlapping roots, skips common build/cache/environment directories, and marks
failed runs for retry. File/app ranking uses exact, prefix, substring,
extension/path, and fuzzy name matching; it never compares those scores with
semantic screenshot scores.

Scanned PDF pages containing only whitespace or control characters are rendered
at 150 DPI and OCRed one page at a time through local Tesseract. Pages with
meaningful selectable text are preserved and are not OCRed, even when scanned
imagery is also present. PDF OCR is bounded to 4096x4096 pixels, 16 million
pixels, 30 seconds per page, and a configurable default of 200 OCR pages.
Whole-document replacement is atomic: transient failures preserve the previous
complete document, while new incomplete documents receive `ocr_required`
without searchable partial pages. pypdfium2 5.14.0 and its PDFium/component
notices are included under `licenses/pypdfium2/5.14.0`.

This is a standalone application folder, not an installer. It does not install Tesseract, create shortcuts, include an uninstaller, or publish a GitHub Release. The onedir build and direct executable startup must be verified on a clean Windows machine before distribution; Qt plugins, PDFium, and FastEmbed/ONNX runtime loading should be checked there as well.

## Tests

Run the built-in unittest suite headlessly:

```powershell
$env:PYTHONPATH = "src"
$env:QT_QPA_PLATFORM = "offscreen"
$env:OPENBLAS_NUM_THREADS = "1"
$env:OMP_NUM_THREADS = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Desktop tests mock OCR and use fake embeddings. They do not need Tesseract or a model download.

Phase 8 index tests:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m unittest tests.test_unified_index -v
```

## Synthetic retrieval evaluation

The checked-in manifest at `evaluation/queries.json` labels 12 queries against 16 stable screenshot IDs. Generate the PNG fixtures locally and run the live evaluation:

```powershell
.\.venv\Scripts\python.exe scripts\generate_synthetic_dataset.py
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py
```

The generator uses only the Python standard library and fixed bitmap glyphs, so it produces the same PNG content without platform fonts or third-party image packages. The output folder, `evaluation/generated/`, is ignored by Git. The evaluator OCRs every expected image and stops without printing partial metrics if an image, OCR setup, required Python dependency, or semantic model is unavailable.

The report prints Python, pytesseract, Tesseract, scikit-learn, FastEmbed, ONNX Runtime, and model versions, followed by per-query and unweighted macro-average Precision@5, Recall@5, Precision@10, and Recall@10. It prints IDs and aggregate metrics, not OCR text. For an unavailable prerequisite, install the already-declared project dependencies and install/configure Tesseract; the first semantic run needs model download access.

Metric conventions:

- Precision@K is distinct relevant IDs in the first K results divided by K. Missing result slots count as non-relevant.
- Recall@K is distinct relevant IDs in the first K divided by all labeled relevant IDs for that query.
- Duplicate result IDs count once at their first occurrence. A label with no known relevant IDs is invalid.
- Per-query scores are averaged equally within each approach. Keyword matches have no relevance score, so screenshot ID order is only a deterministic tie-break; TF-IDF and semantic rankings retain their existing order.

This small synthetic benchmark is for comparing behavior on controlled text scenarios. It does not establish retrieval quality on a user's private screenshot library, and results also depend on OCR and model versions.

## Privacy and repository hygiene

Do not put personal screenshots, OCR dumps, or private paths in the repository. Use `evaluation/generated/` only for the script-generated synthetic PNGs; it is ignored by Git. Keep real screenshot collections outside the repository (the `/screenshots/` and `/data/` directories are ignored), and do not add evaluation output containing user data. The checked-in query labels and generator text are synthetic.

## Phase 8 module map and limitations

- `src/snapseek/unified_index.py` owns SQLite migration, incremental folder
  indexing, deletion cleanup, and transactional `.lnk` shortcut discovery.
- `src/snapseek/pdf_extractor.py` provides standalone local, page-preserving
  PDF text extraction with metadata and explicit encrypted/corrupt/OCR-required
  statuses. `LocalIndex` persists PDF metadata, page text, and extracted/OCR
  provenance incrementally in SQLite schema version 4 using streaming SHA-256
  fingerprints. Documents
  search uses SQLite FTS5 when available and a bounded SQL fallback otherwise;
  semantic search and password handling are not yet implemented.
- `src/snapseek/unified_search.py` owns category filtering, local ranking, and
  default application launching.
- `src/snapseek/windows_hotkey.py` owns the optional Windows `RegisterHotKey`
  integration.
- `src/snapseek/desktop.py` owns page construction, category UI, native-window
  adaptation, and user-facing operation outcomes.
- `src/snapseek/ui/operations.py` owns scan/search task construction, cached
  snapshot restoration, persistence coordination, and worker lifecycle.
- `src/snapseek/ui/workers.py`, `result_cards.py`, `result_grid.py`, and
  `dialogs.py` own reusable Qt worker and result-presentation components.
- `src/snapseek/ui/pages.py` owns Home, Folder, Stats, and Settings page
  construction and page-local signals.
- `src/snapseek/ui/navigation.py` owns the branded title bar and window-control
  presentation.
- `src/snapseek/ui/theme.py` owns shared visual helpers and stylesheet generation.

The index scans selected folders recursively and continuously monitors the
selected folder for filesystem events after a successful scan. A conservative
background reconciliation also recovers missed creates, modifications, and
deletions; observer failures and temporarily unavailable roots are retried with
bounded backoff and reconciled before monitoring resumes. Use Scan / Rescan as
an immediate manual recovery path when needed. Per-user and machine-wide Start
Menu roots plus per-user and Public Desktop roots are monitored independently.
Start Menu roots are searched recursively for `.lnk` files; Desktop roots
search only top-level `.lnk` files so unrelated development folders are never
traversed. Shortcut changes refresh the application index transactionally.
Known Folder APIs are preferred for Desktop discovery, with environment
fallbacks. Unavailable or unsuccessfully enumerated roots retain their existing
records, while shortcuts at different paths remain separate results.
The initial cached-scan landing layout is one-shot, so minimizing and restoring
after a search preserves the compact search bar, query, category, and results.
Shortcut targets are
delegated to Windows when launched, so invalid shortcuts fail at open time
without crashing indexing. Native backdrop, shortcut conflict, clean-machine
packaging, and large-library latency still require manual validation.
PDF extraction and persistence use `pypdf` locally and never modify source
files. PDFs are refreshed on create, modify, rename, delete, and reconciliation
events. Page boundaries, metadata, extraction status, and a streaming content
fingerprint are stored transactionally. Unchanged PDFs skip extraction and OCR;
transient access or OCR failures preserve the previous valid pages. Empty
pages are rendered by pypdfium2 and OCRed locally through Tesseract, while
pages with meaningful selectable text are never OCRed. PDF keyword search
returns page numbers and short safely escaped snippets, never extracts during
search, and limits results to three pages per document. PDF semantic
embeddings, password handling, and exact-page viewer navigation remain future
stages.
