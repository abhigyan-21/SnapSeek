# SnapSeek - Progress

## Completed
- [x] Project setup: basic `pyproject.toml`, `src/snapseek/` package, and `.gitignore`
- [x] Phase 1: screenshot inventory, filesystem error handling, and unit tests
- [x] Phase 2 unit 1: standalone OCR component and mocked unit tests
- [x] Phase 2 unit 2: integrate OCR into application output and handle failures
- [x] Phase 2 real OCR check: user reported successful Tesseract OCR on a screenshot
- [x] Phase 3: in-memory keyword search
- [x] Phase 4: TF-IDF ranking over in-memory OCR results
- [ ] Phase 5: semantic search
- [ ] Phase 6: Streamlit interface
- [ ] Phase 7: evaluation and documentation

## Current task
Phase 1 through Phase 4 are complete. `main.py` accepts one positional query and still reads the screenshot directory from `SNAPSEEK_SCREENSHOT_DIR`. It validates the query, scans once, runs OCR once per image, pairs successful text with `ImageMetadata`, and ranks those in-memory records using TF-IDF. The CLI has no search-mode selector. The separate Phase 3 `search_keyword()` function and behavior remain intact for future hybrid retrieval. Image-specific OCR failures warn and continue; OCR setup failures are reported once and stop further OCR calls. TF-IDF returns positive-score matches, sorts by score and normalized path, and reports empty/no-vocabulary cases as no matches. Search output is not persisted or cached.

## Known issues
- The package is in a `src/` layout. Running from a checkout requires installing the package or setting `PYTHONPATH=src`.
- Inventory results are printed only; no persistent metadata file is created.
- The user reported a successful real Tesseract OCR check on a screenshot. This was not independently rerun during Phase 3 implementation; Phase 3 automated tests mock OCR.
- A vanished file is detected when its metadata `stat()` raises `FileNotFoundError`; other filesystem errors abort the scan and are reported by `main()`.
- OCR/search records and results exist in memory only for the current run; no text or metadata is persisted.
- TF-IDF ranking uses default word unigrams; phrase queries do not enforce exact word order or adjacency. Single-character terms are omitted by the default tokenizer. OCR recognition quality across image types remains unevaluated beyond the user-reported screenshot check.

## Test results
- Project dependency setup: `.venv\Scripts\python.exe -m pip install -e .` succeeded; scikit-learn 1.9.1 was installed in the project `.venv`.
- PowerShell test environment: `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, `PATH` prefixed with `.venv\Scripts`, and `PYTHONPATH=src`.
- `$env:PYTHONPATH = "src"; python -m unittest discover -s tests -p "test_search.py" -v`: 15 tests passed.
- `$env:PYTHONPATH = "src"; python -m unittest discover -s tests -p "test_main.py" -v`: 9 tests passed.
- `$env:PYTHONPATH = "src"; python -m unittest discover -s tests -v`: 46 tests passed across Phase 1–4.
- Test commands ran sequentially with the project `.venv` active and `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1` to avoid environment thread/memory exhaustion.
- Automated OCR integration tests use mocks and temporary files. The user-reported real Tesseract check was not rerun in Phase 4; TF-IDF tests use scikit-learn but do not perform OCR.

## Next task
Wait for developer review and explicit confirmation before beginning Phase 5 (semantic search).
