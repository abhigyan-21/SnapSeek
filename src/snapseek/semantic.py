"""Semantic search over existing OCR text records using local embeddings."""

from dataclasses import dataclass
from importlib import import_module
import math
from typing import Iterable, Protocol, Sequence

from snapseek.search import OCRTextRecord


MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class SemanticSearchError(RuntimeError):
    """Base class for semantic search setup and inference errors."""


class SemanticDependencyError(SemanticSearchError):
    """Raised when the FastEmbed Python package is unavailable."""


class SemanticModelError(SemanticSearchError):
    """Raised when the local embedding model cannot be loaded or downloaded."""


class SemanticInferenceError(SemanticSearchError):
    """Raised when embedding or similarity inference fails."""


@dataclass(frozen=True)
class SemanticSearchResult:
    """An OCR record paired with its semantic cosine similarity score."""

    record: OCRTextRecord
    score: float


class TextEncoder(Protocol):
    """Small interface implemented by FastEmbed and deterministic test fakes."""

    def embed(self, documents: Iterable[str]) -> Iterable[Sequence[float]]:
        """Encode document strings into vectors."""

    def query_embed(self, query: str) -> Iterable[Sequence[float]]:
        """Encode one query string into a vector."""


def _is_expected_model_runtime_error(error: Exception) -> bool:
    """Recognize expected network, model-loading, and ONNX runtime failures."""
    if isinstance(error, (OSError, RuntimeError)):
        return True
    module_name = type(error).__module__
    return module_name.startswith(("httpx", "requests", "huggingface_hub", "onnxruntime"))


def load_semantic_encoder() -> TextEncoder:
    """Load FastEmbed and its configured model, downloading it if needed.

    Imports are lazy so keyword and TF-IDF search remain importable without
    FastEmbed. The returned encoder is intended to be reused for this run.
    """
    try:
        fastembed = import_module("fastembed")
    except ImportError as error:
        raise SemanticDependencyError(
            "Semantic search requires FastEmbed. Install SnapSeek's Python "
            "dependencies with `pip install -e .`."
        ) from error

    try:
        return fastembed.TextEmbedding(model_name=MODEL_NAME)
    except Exception as error:
        if not _is_expected_model_runtime_error(error):
            raise
        raise SemanticModelError(
            f"Could not load semantic model {MODEL_NAME!r}. Check your internet "
            "connection for the first download, or verify the local model cache."
        ) from error


def _as_vector(value: Sequence[float], *, label: str) -> tuple[float, ...]:
    """Convert and validate an encoder result before cosine scoring."""
    try:
        vector = tuple(float(component) for component in value)
    except (TypeError, ValueError) as error:
        raise SemanticInferenceError(f"The model returned an invalid {label} vector.") from error

    if not vector or not all(math.isfinite(component) for component in vector):
        raise SemanticInferenceError(f"The model returned an invalid {label} vector.")
    return vector


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Compute cosine similarity without adding another numerical dependency."""
    if len(left) != len(right):
        raise SemanticInferenceError("The model returned vectors with different dimensions.")

    left_norm = math.sqrt(math.fsum(component * component for component in left))
    right_norm = math.sqrt(math.fsum(component * component for component in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0

    dot_product = math.fsum(a * b for a, b in zip(left, right, strict=True))
    return dot_product / (left_norm * right_norm)


def search_semantic(
    query: str,
    records: Iterable[OCRTextRecord],
    *,
    encoder: TextEncoder | None = None,
) -> list[SemanticSearchResult]:
    """Rank nonempty OCR records by cosine similarity to a query.

    This function only uses the supplied records. It does not scan directories
    or run OCR. No similarity cutoff is applied; all nonempty documents are
    returned, with path-based tie-breaking for equal scores.
    """
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Search query must not be empty or whitespace-only.")

    usable_records = [record for record in records if record.ocr_text.strip()]
    if not usable_records:
        return []

    if encoder is None:
        encoder = load_semantic_encoder()

    try:
        document_vectors = [
            _as_vector(vector, label="document")
            for vector in encoder.embed(record.ocr_text for record in usable_records)
        ]
        query_vectors = list(encoder.query_embed(normalized_query))
    except Exception as error:
        if isinstance(error, SemanticSearchError):
            raise
        if _is_expected_model_runtime_error(error):
            raise SemanticInferenceError(
                f"Semantic embedding inference failed: {error}"
            ) from error
        raise

    if len(document_vectors) != len(usable_records):
        raise SemanticInferenceError(
            "The model returned a different number of document vectors than documents."
        )
    if len(query_vectors) != 1:
        raise SemanticInferenceError("The model did not return exactly one query vector.")

    query_vector = _as_vector(query_vectors[0], label="query")
    results = [
        SemanticSearchResult(
            record=record,
            score=_cosine_similarity(query_vector, document_vector),
        )
        for record, document_vector in zip(usable_records, document_vectors, strict=True)
    ]
    return sorted(
        results,
        key=lambda result: (
            -result.score,
            result.record.image.path.as_posix().casefold(),
            result.record.image.path.as_posix(),
        ),
    )
