# SnapSeek — System Flow

> Purpose: Explain how data moves through SnapSeek and what each component is responsible for.
>
> This document describes the intended design. Sections are marked **Planned** until the behavior has actually been implemented and tested.

## 1. Product flow

The user selects a directory containing screenshots through the planned Streamlit UI. SnapSeek discovers supported image files, extracts metadata, reads visible text with OCR, and makes the resulting text searchable. Search results link back to the original images for preview.

The current terminal query uses semantic similarity over OCR text only; it does not search filenames or image pixels. TF-IDF and keyword search remain available internally as separate retrieval functions. The terminal accepts one query and does not ask the user to choose a search mode.

## 2. High-level architecture

```text
Screenshot directory (environment variable in Phase 1; UI selection later)
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
             (main.py)
                |
                v
       Semantic ranking
         (src/snapseek/semantic.py)
                |
                v
       Results: filename, image path,
       OCR text, and semantic similarity
                |
                v
       Streamlit UI (later phase)
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
Print each image and total image count
        |
        +-- vanished file: warn and skip
        +-- other scan filesystem error: report and exit non-zero
```

OCR and the UI are deliberately absent from Phase 1.

## 3. Module responsibilities

| Module | Responsibility | Planned phase |
|---|---|---|
| `main.py` | Validates the CLI query and configuration, scans images, loads semantic search before OCR, coordinates OCR per image, and prints ranked results and a summary | Phases 1–5 (implemented) |
| `src/snapseek/config.py` | Read `SNAPSEEK_SCREENSHOT_DIR` and report missing or invalid directory configuration | Phase 1 (implemented) |
| `src/snapseek/scanner.py` | Discover direct-child PNG, JPG, JPEG, and WEBP files case-insensitively; collect file metadata | Phase 1 (implemented) |
| `src/snapseek/__init__.py` | Mark the SnapSeek source package | Phase 1 (implemented) |
| `src/snapseek/ocr.py` | Extracts text for one `ImageMetadata` using local Tesseract through `pytesseract`; reports setup and image-specific failures | Phase 2 (implemented) |
| `src/snapseek/indexer.py` | Coordinate discovery, OCR, and creation/update of indexed records | Phases 2 onward |
| `src/snapseek/search.py` | Keyword substring matching and TF-IDF ranking over supplied OCR records | Phases 3–4 (implemented) |
| `src/snapseek/semantic.py` | Load FastEmbed lazily and rank supplied OCR records with local semantic embeddings | Phase 5 (implemented) |
| `tests/test_scanner.py` | Verify configuration and inventory behavior with temporary directories and files | Phase 1 (implemented) |
| `tests/test_main.py` | Verify application OCR orchestration, output association, continuation, setup errors, and semantic CLI output | Phases 2 and 5 (implemented) |
| `tests/test_search.py` | Verify keyword behavior and TF-IDF ranking, result association, ties, zero scores, and empty inputs | Phases 3 and 4 (implemented) |
| `tests/test_semantic.py` | Verify semantic scoring, record association, ties, errors, and empty inputs with fake embeddings | Phase 5 (implemented) |
| `Streamlit UI` | One search box with retrieval behavior chosen internally, result gallery, OCR snippets, scores, and useful status messages | Phase 6 (planned) |

Create modules gradually. Do not add a module merely to match a diagram if its responsibility is not needed yet.

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
5. Reuse the encoder and generated vectors for this search invocation only; do not persist or cache them across runs.

The model downloads on first use when it is not already cached. A missing FastEmbed package or model load/download failure is reported before OCR starts. OCR text and images remain local; only the public model files are downloaded. Semantic search searches the meaning of extracted text, not objects, diagrams, or image content OCR did not capture. No real-model quality evaluation has been performed.

## 6. Error and edge-case flow

- **Directory missing or not a directory:** Report a configuration error; do not scan unrelated locations.
- **Directory scan filesystem error:** Report the affected directory and advise checking that it exists and is readable; return a non-zero status.
- **Supported image vanishes during metadata collection:** Print a warning naming the path, skip that image, and continue scanning.
- **OCR wrapper unavailable:** The standalone OCR function raises `OCRDependencyError` with installation guidance.
- **Tesseract executable unavailable or unlaunchable:** The standalone OCR function raises `TesseractExecutableError` with PATH/setup or permission guidance.
- **Image-specific OCR failure:** The standalone OCR function raises `ImageOCRError` naming the image; integrated `main.py` warns and continues with the next image.
- **Integrated image-specific OCR failure:** `main.py` warns to standard error and continues with later images; the final status is non-zero if any image failed.
- **Integrated OCR setup failure:** `main.py` reports the setup error once, stops OCR calls, reports unprocessed images in the summary, and returns a non-zero status.
- **TF-IDF dependency unavailable:** TF-IDF search reports an actionable scikit-learn installation error and returns a non-zero status; keyword search itself does not require scikit-learn.
- **FastEmbed unavailable:** Semantic setup reports an actionable dependency error before OCR starts and returns a non-zero status.
- **Semantic model unavailable:** A first-download or local model-load failure is reported before OCR starts; the application does not silently fall back to TF-IDF.
- **Semantic inference failure:** Report a semantic search error and return a non-zero status after OCR; unrelated programming errors are not intentionally converted into semantic errors.
- **Unsupported extension:** Ignore the file.
- **Unreadable/corrupt image:** Record or report the failure and continue with other images.
- **No OCR text:** Keep the image record; represent empty OCR output safely.
- **Empty library:** Show a clear message rather than failing.
- **Empty query:** Do not run a meaningless search; explain what the user can enter.
- **No matches:** Return an empty result set and show a useful message.
- **Source image moved or removed:** Detect an invalid path when practical and avoid pretending the result is still available.
- **Duplicate filenames:** Distinguish files using their paths and, where needed, a stable identifier.

## 7. Evaluation flow (planned)

```text
Manually labeled queries and relevant screenshots
                    |
          +---------+---------+
          |         |         |
          v         v         v
       Keyword    TF-IDF   Semantic
       search     search    search
          |         |         |
          +---------+---------+
                    |
                    v
       Compare Precision@K and Recall@K
                    |
                    v
      Document results and limitations
```

Use the same labeled query set for each approach so the comparison is meaningful. Do not claim that one approach is better without evaluation evidence.

## 8. Development sequence and gates

1. **Phase 1 — Inventory (implemented):** read the directory from `SNAPSEEK_SCREENSHOT_DIR`, scan direct children for supported extensions, collect metadata, print inventory and total count, and test with `unittest` temporary directories.
   - **Gate:** unit tests cover supported and unsupported files, case differences, sorting, empty and missing paths, ignored subfolders, a simulated disappearing image, and a simulated directory listing error. Manual use against a screenshot directory is still a user-run action.
2. **Phase 2 — OCR:** configure Tesseract, extract and record text, isolate failures.
   - **Units 1 and 2 implemented:** standalone text extraction and application integration, with mocked tests for output association, empty results, per-image continuation, and setup failures.
   - **Gate:** the user reports a successful real Tesseract run on a screenshot. Unit tests do not establish general Tesseract recognition quality.
3. **Phase 3 — Keyword search (implemented):** retain the internal keyword function for case-insensitive OCR-text substring matching.
4. **Phase 4 — TF-IDF (implemented):** the same positional CLI query uses TF-IDF ranking internally, without a manual mode selector. Keyword search remains a separate tested function for future hybrid retrieval. Scores are descending, zero-score records are excluded, and ties use normalized image paths. No persistence, cache, hybrid ranking, or semantic search is included.
5. **Phase 5 — Semantic search (implemented):** FastEmbed embeddings and cosine ranking are the default behind the existing single query; TF-IDF and keyword functions remain available internally. The model and vectors are reused only during the current run; there is no persistent cache or threshold.
6. **Phase 6 — Streamlit (planned):** one search box, image previews, snippets, scores, and status/error states; the retrieval engine chooses behavior internally.
7. **Phase 7 — Evaluation and documentation:** labeled queries, metrics, setup guide, sanitized repository.

Do not move to the next phase until the current gate has been demonstrated and the developer confirms proceeding.

## 9. Keep this file current

After each meaningful implementation:
- Update the flow to match actual behavior, not just the intended design.
- Label unimplemented features as `Planned`.
- Note important error paths and caching behavior.
- Keep architecture changes consistent with `Decisions.md`.

## Phase 3–5 implementation status (current)

The current terminal flow accepts one positional query while retaining `SNAPSEEK_SCREENSHOT_DIR` for directory configuration. `main.py` validates the nonblank query before reading configuration, scans once, calls OCR once per discovered image until a setup error occurs, and creates an `OCRTextRecord` for each successful OCR result. Image-specific OCR errors warn and continue; missing OCR setup reports one error and stops further OCR attempts. The OCR summary counts discovered images, successes, failures, and unprocessed images. The entry point passes the successful records to `search_tfidf()`; it does not rescan or rerun OCR during search.

`search_keyword()` keeps its Phase 3 case-insensitive substring behavior and `search_tfidf()` keeps its Phase 4 ranking behavior. The Phase 5 CLI instead calls `search_semantic()` with the successful OCR records. Semantic results retain their source record and display filename, path, similarity score, and OCR text. Empty/whitespace OCR documents are skipped; empty collections return no results. All other nonempty documents are returned without a threshold and sorted by descending cosine similarity, then normalized path. FastEmbed and the model load before OCR; vectors are reused only for the current invocation. A blank query is rejected before directory access. The user does not select a search mode.

The user reported a successful real Tesseract OCR run on a screenshot; that remains user-reported verification. Phase 5 semantic tests use fake embeddings and do not download or evaluate the real model. No real-model semantic quality evaluation has been performed. Hybrid retrieval, persistence, and UI remain future work.
