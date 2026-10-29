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
- **Decision:** Implement the phases in this order: image inventory, OCR, keyword search, TF-IDF, semantic search, Streamlit UI, evaluation/documentation.
- **Rationale:** Each phase establishes a testable baseline for the next. More advanced search must not conceal basic indexing problems.
- **Consequences:** Do not implement OCR, TF-IDF, embeddings, or Streamlit during the initial inventory task.

### D-005 — Start with a small module structure
- **Status:** Accepted by PRD
- **Context:** Separating responsibilities is useful, but creating abstractions too early adds complexity.
- **Decision:** Use the `src/snapseek/` package and create modules only when needed: `scanner.py`, `ocr.py`, `search.py`, `semantic.py`, and `indexer.py`.
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
- **Decision:** Defer interactive directory selection to the planned Streamlit UI phase rather than introducing a command-line argument for the first version.
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
