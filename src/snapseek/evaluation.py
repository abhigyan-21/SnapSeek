"""Reproducible ranking evaluation for the existing search functions."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Sequence

from snapseek.search import (
    OCRTextRecord,
    RankedSearchResult,
    search_keyword,
    search_tfidf,
)
from snapseek.semantic import (
    SemanticSearchResult,
    TextEncoder,
    search_semantic,
)


APPROACHES = ("keyword", "tfidf", "semantic")
DEFAULT_CUTOFFS = (5, 10)


class EvaluationDataError(ValueError):
    """Raised when an evaluation manifest or relevance label is invalid."""


@dataclass(frozen=True)
class EvaluationQuery:
    """One query and the stable screenshot IDs judged relevant to it."""

    query_id: str
    query: str
    relevant_ids: tuple[str, ...]


@dataclass(frozen=True)
class MetricScore:
    """Precision and recall at one result cutoff."""

    precision: float
    recall: float


@dataclass(frozen=True)
class QueryEvaluation:
    """Per-query metrics for one retrieval approach."""

    query_id: str
    query: str
    approach: str
    metrics: dict[int, MetricScore]


def load_label_manifest(
    manifest_path: str | Path, screenshot_ids: Iterable[str]
) -> tuple[EvaluationQuery, ...]:
    """Load and validate query labels against the generated screenshot IDs."""
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationDataError(f"Could not read label manifest: {error}") from error

    if not isinstance(data, dict) or set(data) != {"dataset", "queries"}:
        raise EvaluationDataError("Manifest must contain only 'dataset' and 'queries'.")
    if not isinstance(data["dataset"], str) or not data["dataset"].strip():
        raise EvaluationDataError("Manifest 'dataset' must be a non-empty string.")
    if not isinstance(data["queries"], list) or not data["queries"]:
        raise EvaluationDataError("Manifest 'queries' must be a non-empty list.")

    known_ids = set(screenshot_ids)
    parsed: list[EvaluationQuery] = []
    seen_query_ids: set[str] = set()
    for index, item in enumerate(data["queries"]):
        label = f"queries[{index}]"
        if not isinstance(item, dict) or set(item) != {"id", "query", "relevant_ids"}:
            raise EvaluationDataError(
                f"{label} must contain only 'id', 'query', and 'relevant_ids'."
            )
        query_id, query, relevant = item["id"], item["query"], item["relevant_ids"]
        if not isinstance(query_id, str) or not query_id.strip():
            raise EvaluationDataError(f"{label}.id must be a non-empty string.")
        if query_id in seen_query_ids:
            raise EvaluationDataError(f"Duplicate query ID: {query_id!r}.")
        seen_query_ids.add(query_id)
        if not isinstance(query, str) or not query.strip():
            raise EvaluationDataError(f"{label}.query must be a non-empty string.")
        if not isinstance(relevant, list) or not relevant:
            raise EvaluationDataError(f"{label}.relevant_ids must be a non-empty list.")
        if any(not isinstance(value, str) or not value.strip() for value in relevant):
            raise EvaluationDataError(f"{label}.relevant_ids must contain non-empty strings.")
        if len(set(relevant)) != len(relevant):
            raise EvaluationDataError(f"{label}.relevant_ids contains duplicate IDs.")
        unknown = sorted(set(relevant) - known_ids)
        if unknown:
            raise EvaluationDataError(
                f"{label}.relevant_ids contains unknown screenshot IDs: {', '.join(unknown)}."
            )
        parsed.append(EvaluationQuery(query_id, query, tuple(relevant)))
    return tuple(parsed)


def _screenshot_id(record: OCRTextRecord) -> str:
    return record.image.path.stem


def _unique_ids(ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for screenshot_id in ids:
        if screenshot_id not in seen:
            seen.add(screenshot_id)
            ordered.append(screenshot_id)
    return ordered


def keyword_ranked_ids(results: Iterable[OCRTextRecord]) -> list[str]:
    """Normalize keyword matches; ID order breaks ties, not relevance scores."""
    return sorted(_unique_ids(_screenshot_id(record) for record in results))


def tfidf_ranked_ids(results: Iterable[RankedSearchResult]) -> list[str]:
    """Extract IDs while preserving TF-IDF's existing ranking."""
    return _unique_ids(_screenshot_id(result.record) for result in results)


def semantic_ranked_ids(results: Iterable[SemanticSearchResult]) -> list[str]:
    """Extract IDs while preserving semantic search's existing ranking."""
    return _unique_ids(_screenshot_id(result.record) for result in results)


def calculate_metrics(
    ranked_ids: Iterable[str],
    relevant_ids: Iterable[str],
    cutoffs: Sequence[int] = DEFAULT_CUTOFFS,
) -> dict[int, MetricScore]:
    """Calculate fixed-denominator Precision@K and Recall@K.

    Repeated result IDs count only at their first rank. Missing result slots
    count as non-relevant for precision because the denominator is always K.
    """
    relevant = set(relevant_ids)
    if not relevant:
        raise EvaluationDataError("A query must have at least one relevant screenshot.")
    if not cutoffs or any(not isinstance(k, int) or isinstance(k, bool) or k <= 0 for k in cutoffs):
        raise ValueError("Cutoffs must be positive integers.")
    if len(set(cutoffs)) != len(cutoffs):
        raise ValueError("Cutoffs must not contain duplicates.")

    ranking = _unique_ids(ranked_ids)
    return {
        k: MetricScore(
            precision=sum(screenshot_id in relevant for screenshot_id in ranking[:k]) / k,
            recall=sum(screenshot_id in relevant for screenshot_id in ranking[:k]) / len(relevant),
        )
        for k in cutoffs
    }


def evaluate_records(
    records: Sequence[OCRTextRecord],
    queries: Sequence[EvaluationQuery],
    *,
    encoder: TextEncoder,
    cutoffs: Sequence[int] = DEFAULT_CUTOFFS,
) -> tuple[QueryEvaluation, ...]:
    """Run all existing retrieval functions over the same supplied OCR data."""
    record_ids = [_screenshot_id(record) for record in records]
    if len(set(record_ids)) != len(record_ids):
        raise EvaluationDataError("OCR records must have unique screenshot IDs.")
    known_ids = set(record_ids)
    for item in queries:
        if not item.query.strip() or not item.relevant_ids:
            raise EvaluationDataError("Every query needs text and at least one relevant ID.")
        unknown = set(item.relevant_ids) - known_ids
        if unknown:
            raise EvaluationDataError(
                f"Query {item.query_id!r} contains unknown screenshot IDs: "
                f"{', '.join(sorted(unknown))}."
            )

    evaluations: list[QueryEvaluation] = []
    for evaluation_query in queries:
        keyword_results = search_keyword(evaluation_query.query, records)
        tfidf_results = search_tfidf(evaluation_query.query, records)
        semantic_results = search_semantic(
            evaluation_query.query, records, encoder=encoder
        )
        normalized = {
            "keyword": keyword_ranked_ids(keyword_results),
            "tfidf": tfidf_ranked_ids(tfidf_results),
            "semantic": semantic_ranked_ids(semantic_results),
        }
        for approach in APPROACHES:
            evaluations.append(
                QueryEvaluation(
                    query_id=evaluation_query.query_id,
                    query=evaluation_query.query,
                    approach=approach,
                    metrics=calculate_metrics(
                        normalized[approach], evaluation_query.relevant_ids, cutoffs
                    ),
                )
            )
    return tuple(evaluations)


def macro_averages(
    evaluations: Sequence[QueryEvaluation],
    cutoffs: Sequence[int] = DEFAULT_CUTOFFS,
) -> dict[str, dict[int, MetricScore]]:
    """Return unweighted per-query macro averages for each approach."""
    grouped = {
        approach: [row for row in evaluations if row.approach == approach]
        for approach in APPROACHES
    }
    if any(not rows for rows in grouped.values()):
        raise EvaluationDataError("Macro averages require results for every approach.")
    expected_query_ids: set[str] | None = None
    for approach, rows in grouped.items():
        query_ids = [row.query_id for row in rows]
        if len(set(query_ids)) != len(query_ids):
            raise EvaluationDataError(
                f"Macro averages contain duplicate queries for {approach}."
            )
        current_ids = set(query_ids)
        if expected_query_ids is not None and current_ids != expected_query_ids:
            raise EvaluationDataError(
                "Macro averages require the same query set for every approach."
            )
        expected_query_ids = current_ids
        if any(k not in row.metrics for row in rows for k in cutoffs):
            raise EvaluationDataError("Per-query results are missing a requested cutoff.")
    return {
        approach: {
            k: MetricScore(
                precision=sum(row.metrics[k].precision for row in rows) / len(rows),
                recall=sum(row.metrics[k].recall for row in rows) / len(rows),
            )
            for k in cutoffs
        }
        for approach, rows in grouped.items()
    }
