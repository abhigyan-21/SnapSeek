# SnapSeek - Progress

## Completed
- [x] Project setup: basic `pyproject.toml`, `src/snapseek/` package, and `.gitignore`
- [x] Phase 1: screenshot inventory, filesystem error handling, and unit tests
- [x] Phase 2 unit 1: standalone OCR component and mocked unit tests
- [x] Phase 2 unit 2: integrate OCR into application output and handle failures
- [x] Phase 2 real OCR check: user reported successful Tesseract OCR on a screenshot
- [x] Phase 3: in-memory keyword search
- [x] Phase 4: TF-IDF ranking over in-memory OCR results
- [x] Phase 5: local semantic search over OCR text
- [ ] Phase 6: Streamlit interface
- [ ] Phase 7: retrieval evaluation and documentation

## Current implementation
`main.py` accepts one positional query and reads the screenshot directory from `SNAPSEEK_SCREENSHOT_DIR`. It validates configuration, scans once, and loads FastEmbed's `sentence-transformers/all-MiniLM-L6-v2` model before OCR when images were found. OCR runs once per image; image-specific failures warn and continue, while OCR setup errors stop processing. Successful text remains paired with the original `ImageMetadata`. Semantic search batch-encodes nonempty OCR documents, encodes the query once, and returns every result sorted by cosine similarity and normalized path. The CLI has one query input and no search-mode selector or fallback to TF-IDF.

`search_keyword()` and `search_tfidf()` retain their prior behavior and remain separately callable. OCR records, vectors, and ranked results are in memory only for the current run. FastEmbed stores its downloaded model files in its local model cache so they can be reused offline; SnapSeek does not persist OCR text or embeddings.

## Known limitations
- The package uses a `src/` layout; running from a checkout requires an editable install or `PYTHONPATH=src`.
- Phase 1 inventory metadata and Phase 2–5 OCR/search records are not persisted by SnapSeek.
- The first use of semantic search requires the model files to be downloaded, or already present in FastEmbed's local cache. Model setup failure returns a nonzero status before OCR begins.
- Semantic ranking has no score threshold yet. All nonempty OCR documents are ranked, so low-relevance results may appear. A threshold should be selected only after labeled evaluation.
- The selected MiniLM model is English-focused and may truncate long OCR text at its input limit. Semantic search covers extracted OCR text only, not visual content OCR misses.
- Model inference runs locally but may add CPU latency. SnapSeek recomputes embeddings on later runs; it does not maintain a persistent embedding cache or index.
- The user reported a successful real Tesseract OCR check on a screenshot. Real-model semantic quality has not been evaluated.

## Validation history
- Phase 1 scanner tests use temporary directories and cover supported/unsupported extensions, case normalization, sorting, empty/missing directories, ignored subfolders, disappearing files, and directory-listing errors.
- Phase 2–4: Phase 4 full suite passed 46 Phase 1–4 tests. OCR was mocked; these tests did not rerun the user-reported real Tesseract check.
- Phase 4 focused test runs: `test_search.py` passed 15 tests and `test_main.py` passed 9 tests.
- Phase 5 dependency setup: `.venv\Scripts\python.exe -m pip install "fastembed>=0.9,<1.0"` completed successfully; FastEmbed 0.9.1 and ONNX Runtime 1.31.1 were installed. The model itself was not downloaded during dependency installation.
- `$env:PYTHONPATH = "src"; $env:OPENBLAS_NUM_THREADS = "1"; $env:OMP_NUM_THREADS = "1"; .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_semantic.py" -v`: 14 tests passed.
- `$env:PYTHONPATH = "src"; $env:OPENBLAS_NUM_THREADS = "1"; $env:OMP_NUM_THREADS = "1"; .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_main.py" -v`: 11 tests passed.
- `$env:PYTHONPATH = "src"; $env:OPENBLAS_NUM_THREADS = "1"; $env:OMP_NUM_THREADS = "1"; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`: 62 tests passed across Phases 1–5.
- Semantic tests use fake embeddings; no real model was downloaded or evaluated. The user-reported real Tesseract check was not rerun.
- Phase 5 `git diff --check` completed with exit code 0; Git printed only its LF-to-CRLF working-copy warnings.

## Next task
Wait for developer review and confirmation before starting Phase 6.
