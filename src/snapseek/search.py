"""In-memory keyword and TF-IDF search over OCR text records."""

from dataclasses import dataclass
from importlib import import_module
from typing import Iterable

from snapseek.scanner import ImageMetadata


@dataclass(frozen=True)
class OCRTextRecord:
    """An image and the OCR text extracted from that image."""

    image: ImageMetadata
    ocr_text: str


class SearchDependencyError(RuntimeError):
    """Raised when the optional TF-IDF search dependency is unavailable."""


@dataclass(frozen=True)
class RankedSearchResult:
    """An OCR record paired with its TF-IDF cosine similarity score."""

    record: OCRTextRecord
    score: float


def search_keyword(query: str, records: Iterable[OCRTextRecord]) -> list[OCRTextRecord]:
    """Return records whose OCR text contains the query, ignoring case.

    Matching is substring based, so a query may be a word or a phrase.
    Input order is preserved. This function only searches supplied records;
    it does not scan files or perform OCR.
    """
    normalized_query = query.strip().casefold()
    if not normalized_query:
        raise ValueError("Search query must not be empty or whitespace-only.")

    return [
        record
        for record in records
        if normalized_query in record.ocr_text.casefold()
    ]


def search_tfidf(
    query: str, records: Iterable[OCRTextRecord]
) -> list[RankedSearchResult]:
    """Rank OCR records by positive TF-IDF cosine similarity.

    TF-IDF dependencies are imported only for this search path, so keyword
    search remains usable without scikit-learn. Results are ordered by
    descending score, then by normalized image path for deterministic ties.
    """
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Search query must not be empty or whitespace-only.")

    image_records = list(records)
    if not image_records:
        return []

    try:
        text_module = import_module("sklearn.feature_extraction.text")
        metrics_module = import_module("sklearn.metrics.pairwise")
    except ImportError as error:
        raise SearchDependencyError(
            "TF-IDF search requires scikit-learn. Install SnapSeek's Python "
            "dependencies with `pip install -e .`."
        ) from error

    vectorizer = text_module.TfidfVectorizer()
    corpus = [record.ocr_text for record in image_records]
    analyzer = vectorizer.build_analyzer()
    if not any(analyzer(document) for document in corpus):
        return []

    document_vectors = vectorizer.fit_transform(corpus)
    query_vector = vectorizer.transform([normalized_query])
    if query_vector.nnz == 0:
        return []

    scores = metrics_module.cosine_similarity(
        query_vector, document_vectors
    ).ravel()
    ranked = [
        RankedSearchResult(record=record, score=float(score))
        for record, score in zip(image_records, scores)
        if score > 0.0
    ]
    return sorted(
        ranked,
        key=lambda result: (
            -result.score,
            result.record.image.path.as_posix().casefold(),
            result.record.image.path.as_posix(),
        ),
    )
