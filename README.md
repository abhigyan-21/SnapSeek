# SnapSeek

SnapSeek searches local screenshot text. It scans image files, extracts visible text with Tesseract OCR, and ranks the resulting records with local semantic embeddings. The repository also includes separate keyword and TF-IDF search functions and a synthetic retrieval evaluation.

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

This is a standalone application folder, not an installer. It does not install Tesseract, create shortcuts, include an uninstaller, or publish a GitHub Release. The onedir build and direct executable startup must be verified on a clean Windows machine before distribution; Qt plugins and FastEmbed/ONNX runtime loading should be checked there as well.

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
