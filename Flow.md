# SnapSeek — System Flow

> Purpose: Explain how data moves through SnapSeek and what each component is responsible for.
>
> This document describes the intended design. Sections are marked **Planned** until the behavior has actually been implemented and tested.

## 1. Product flow

The user selects a directory containing screenshots through the planned Streamlit UI. SnapSeek discovers supported image files, extracts metadata, reads visible text with OCR, and makes the resulting text searchable. Search results link back to the original images for preview.

The initial search operates on text found in filenames and OCR output. Semantic search, when added, will search the meaning of extracted text—not every visual element in the image.

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
       Indexed screenshot records
          (src/snapseek/indexer.py)
                |
                v
       Search and ranking
         (src/snapseek/search.py)
                |
                v
       Results: image path, filename,
       OCR snippet, score (when relevant)
                |
                v
       Streamlit UI (later phase)
```

**Implemented Phase 1 flow:** `main.py` reads `SNAPSEEK_SCREENSHOT_DIR` and validates that it names an existing directory. The scanner inspects direct child files only, collects metadata for supported image extensions, and the entry point prints each record and the total count. If a supported file disappears before its size is read, the scanner writes a path-specific warning to standard error, skips that file, and continues. Other filesystem errors from scanning are reported by `main.py` with an actionable message and a non-zero exit status; configuration errors remain separately identified.

**Implemented Phase 2 units 1 and 2:** `src/snapseek/ocr.py` exposes `extract_text(ImageMetadata) -> str`, and `main.py` now calls it for each image returned by `scan_images()`. The entry point prints each successful result with its image path; empty OCR output is shown as `(no text detected)` and counted as success. An `ImageOCRError` produces a path-specific warning on standard error, then processing continues. A missing `pytesseract` package or unavailable/unlaunchable Tesseract executable produces one actionable setup error, stops further OCR calls, and returns a non-zero status. The summary reports discovered images, OCR successes, image failures, and images not processed. OCR text remains terminal output only; there is no persistence or search yet. Unit tests mock OCR, so real Tesseract recognition and OCR quality have not been verified.

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
| `main.py` | Validates configuration, scans images, coordinates OCR per image, and prints results and a summary | Phases 1 and 2 (implemented through Unit 2) |
| `src/snapseek/config.py` | Read `SNAPSEEK_SCREENSHOT_DIR` and report missing or invalid directory configuration | Phase 1 (implemented) |
| `src/snapseek/scanner.py` | Discover direct-child PNG, JPG, JPEG, and WEBP files case-insensitively; collect file metadata | Phase 1 (implemented) |
| `src/snapseek/__init__.py` | Mark the SnapSeek source package | Phase 1 (implemented) |
| `src/snapseek/ocr.py` | Extracts text for one `ImageMetadata` using local Tesseract through `pytesseract`; reports setup and image-specific failures | Phase 2 (implemented) |
| `src/snapseek/indexer.py` | Coordinate discovery, OCR, and creation/update of indexed records | Phases 2 onward |
| `src/snapseek/search.py` | Implement keyword search first, then TF-IDF and optional semantic retrieval | Phases 3–5 |
| `tests/test_scanner.py` | Verify configuration and inventory behavior with temporary directories and files | Phase 1 (implemented) |
| `tests/test_main.py` | Verify application OCR orchestration, output association, continuation, and setup failures with mocks | Phase 2 (implemented) |
| `tests/` | Verify behavior, including corrupt images and search edge cases | Later phases |
| `Streamlit UI` | Search input, search mode selector, result gallery, OCR snippets, scores, and useful status messages | Phase 6 |

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

### Level 1 — Keyword search (planned)

1. Receive a query.
2. Handle an empty or whitespace-only query safely.
3. Normalize query text, filename, and OCR text for case-insensitive matching.
4. Match the query against filename and OCR text.
5. Rank matches using simple, documented rules.
6. Return matching records with valid source image paths.

### Level 2 — TF-IDF search (planned)

1. Build a searchable text corpus from indexed records.
2. Fit a TF-IDF vectorizer on that corpus.
3. Transform the user's query with the same fitted vectorizer.
4. Compute cosine similarity between the query vector and document vectors.
5. Sort by descending similarity and return a configurable number of results.
6. Reuse the fitted vectorizer for later queries until the corpus changes.

**Important:** Fit the vectorizer on the corpus, not on each query. If the corpus changes, the fitted representation may need to be rebuilt.

### Level 3 — Semantic search (planned and optional)

1. Load a pretrained Sentence Transformers model.
2. Encode each screenshot's OCR text.
3. Encode the user's query.
4. Compare embeddings and rank results by semantic similarity.
5. Cache embeddings and reuse them until the source text changes.
6. If the model is unavailable, keep the non-semantic search modes usable.

**Limitation:** In the initial design, semantic search searches the meaning of extracted text. It does not automatically understand objects, diagrams, or other image content that OCR did not capture.

## 6. Error and edge-case flow

- **Directory missing or not a directory:** Report a configuration error; do not scan unrelated locations.
- **Directory scan filesystem error:** Report the affected directory and advise checking that it exists and is readable; return a non-zero status.
- **Supported image vanishes during metadata collection:** Print a warning naming the path, skip that image, and continue scanning.
- **OCR wrapper unavailable:** The standalone OCR function raises `OCRDependencyError` with installation guidance.
- **Tesseract executable unavailable or unlaunchable:** The standalone OCR function raises `TesseractExecutableError` with PATH/setup or permission guidance.
- **Image-specific OCR failure:** The standalone OCR function raises `ImageOCRError` naming the image; integrated `main.py` warns and continues with the next image.
- **Integrated image-specific OCR failure:** `main.py` warns to standard error and continues with later images; the final status is non-zero if any image failed.
- **Integrated OCR setup failure:** `main.py` reports the setup error once, stops OCR calls, reports unprocessed images in the summary, and returns a non-zero status.
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
   - **Gate:** real OCR on sample images and verification of OCR quality/environment setup remain pending. Unit tests do not establish Tesseract recognition quality.
3. **Phase 3 — Keyword search:** normalize text, match filenames/OCR text, test edge cases.
4. **Phase 4 — TF-IDF:** vectorization, cosine similarity, ranking, baseline comparison.
5. **Phase 5 — Semantic search:** embeddings, caching, comparison with TF-IDF.
6. **Phase 6 — Streamlit:** search modes, image previews, snippets, status/error states.
7. **Phase 7 — Evaluation and documentation:** labeled queries, metrics, setup guide, sanitized repository.

Do not move to the next phase until the current gate has been demonstrated and the developer confirms proceeding.

## 9. Keep this file current

After each meaningful implementation:
- Update the flow to match actual behavior, not just the intended design.
- Label unimplemented features as `Planned`.
- Note important error paths and caching behavior.
- Keep architecture changes consistent with `Decisions.md`.
