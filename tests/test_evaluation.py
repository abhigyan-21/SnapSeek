"""Tests for evaluation labels, adapters, and ranking metrics."""

import json
import tempfile
import unittest
from pathlib import Path

from snapseek.evaluation import (
    EvaluationDataError,
    EvaluationQuery,
    calculate_metrics,
    evaluate_records,
    keyword_ranked_ids,
    load_label_manifest,
    macro_averages,
    semantic_ranked_ids,
    tfidf_ranked_ids,
)
from snapseek.scanner import ImageMetadata
from snapseek.search import OCRTextRecord, RankedSearchResult
from snapseek.semantic import SemanticSearchResult


class MetricTests(unittest.TestCase):
    def test_fixed_denominator_and_duplicate_results_at_first_rank(self) -> None:
        scores = calculate_metrics(["a", "a", "b"], ["a", "b", "c"])

        self.assertAlmostEqual(scores[5].precision, 2 / 5)
        self.assertAlmostEqual(scores[5].recall, 2 / 3)
        self.assertAlmostEqual(scores[10].precision, 2 / 10)
        self.assertAlmostEqual(scores[10].recall, 2 / 3)

    def test_no_relevant_ids_and_invalid_cutoffs_are_rejected(self) -> None:
        with self.assertRaisesRegex(EvaluationDataError, "at least one"):
            calculate_metrics(["a"], [])
        for cutoffs in ((0,), (-1,), (5, 5)):
            with self.subTest(cutoffs=cutoffs), self.assertRaises(ValueError):
                calculate_metrics(["a"], ["a"], cutoffs)


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "queries.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, data) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_valid_manifest_loads_stable_ids_and_queries(self) -> None:
        self.write({
            "dataset": "test",
            "queries": [{"id": "q1", "query": "payment failed", "relevant_ids": ["receipt"]}],
        })

        labels = load_label_manifest(self.path, {"receipt", "calendar"})

        self.assertEqual(labels, (EvaluationQuery("q1", "payment failed", ("receipt",)),))

    def test_manifest_rejects_empty_malformed_duplicate_and_unknown_labels(self) -> None:
        valid_query = {"id": "q1", "query": "query", "relevant_ids": ["known"]}
        invalid_values = (
            {"dataset": "test", "queries": []},
            {"dataset": "test", "queries": [{**valid_query, "relevant_ids": []}]},
            {"dataset": "test", "queries": [{**valid_query, "relevant_ids": ["missing"]}]},
            {"dataset": "test", "queries": [valid_query, valid_query]},
            {"dataset": "test", "queries": [{**valid_query, "unexpected": True}]},
            {"dataset": "test", "queries": [{**valid_query, "relevant_ids": ["known", "known"]}]},
        )
        for manifest in invalid_values:
            with self.subTest(manifest=manifest):
                self.write(manifest)
                with self.assertRaises(EvaluationDataError):
                    load_label_manifest(self.path, {"known"})

    def test_invalid_json_is_rejected(self) -> None:
        self.path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(EvaluationDataError, "Could not read"):
            load_label_manifest(self.path, set())


class RankingAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path("evaluation")
        self.a = OCRTextRecord(
            ImageMetadata("a.png", self.root / "a.png", ".png", 1), "alpha"
        )
        self.z = OCRTextRecord(
            ImageMetadata("z.png", self.root / "z.png", ".png", 1), "zeta"
        )

    def test_keyword_ids_are_sorted_and_duplicates_removed(self) -> None:
        self.assertEqual(keyword_ranked_ids([self.z, self.a, self.a]), ["a", "z"])

    def test_scored_adapters_preserve_api_ranking_and_deduplicate(self) -> None:
        tfidf = [
            RankedSearchResult(self.z, 0.9),
            RankedSearchResult(self.a, 0.8),
            RankedSearchResult(self.z, 0.1),
        ]
        semantic = [
            SemanticSearchResult(self.a, 0.7),
            SemanticSearchResult(self.z, 0.6),
        ]

        self.assertEqual(tfidf_ranked_ids(tfidf), ["z", "a"])
        self.assertEqual(semantic_ranked_ids(semantic), ["a", "z"])


class DeterministicEvaluationTests(unittest.TestCase):
    class FakeEncoder:
        vectors = {
            "Payment declined": (0.9, 0.1),
            "Transfer failed after payment": (0.8, 0.2),
            "Weather forecast for tomorrow": (0.0, 1.0),
        }
        query_vectors = {"payment": (1.0, 0.0), "weather forecast": (0.0, 1.0)}

        def embed(self, documents):
            return [self.vectors[text] for text in documents]

        def query_embed(self, query):
            return [self.query_vectors[query]]

    def setUp(self) -> None:
        root = Path("synthetic")
        self.records = [
            OCRTextRecord(ImageMetadata("z_payment.png", root / "z_payment.png", ".png", 1), "Payment declined"),
            OCRTextRecord(ImageMetadata("a_transfer.png", root / "a_transfer.png", ".png", 1), "Transfer failed after payment"),
            OCRTextRecord(ImageMetadata("m_weather.png", root / "m_weather.png", ".png", 1), "Weather forecast for tomorrow"),
        ]
        self.queries = [
            EvaluationQuery("q_payment", "payment", ("z_payment", "a_transfer")),
            EvaluationQuery("q_weather", "weather forecast", ("m_weather",)),
        ]

    def test_all_three_approaches_are_deterministic_without_ocr_or_model(self) -> None:
        first = evaluate_records(self.records, self.queries, encoder=self.FakeEncoder())
        second = evaluate_records(self.records, self.queries, encoder=self.FakeEncoder())

        self.assertEqual(first, second)
        self.assertEqual(len(first), len(self.queries) * 3)
        keyword_payment = next(
            row for row in first if row.query_id == "q_payment" and row.approach == "keyword"
        )
        self.assertAlmostEqual(keyword_payment.metrics[5].recall, 1.0)
        self.assertAlmostEqual(keyword_payment.metrics[5].precision, 2 / 5)
        self.assertEqual(set(macro_averages(first)), {"keyword", "tfidf", "semantic"})

    def test_evaluation_rejects_unknown_labels_and_macro_requires_shared_queries(self) -> None:
        invalid = [EvaluationQuery("bad", "query", ("absent",))]
        with self.assertRaisesRegex(EvaluationDataError, "unknown screenshot IDs"):
            evaluate_records(self.records, invalid, encoder=self.FakeEncoder())

        partial = evaluate_records(
            self.records, self.queries[:1], encoder=self.FakeEncoder()
        ) + evaluate_records(
            self.records, self.queries[1:], encoder=self.FakeEncoder()
        )[:2]
        with self.assertRaisesRegex(EvaluationDataError, "same query set"):
            macro_averages(partial)


if __name__ == "__main__":
    unittest.main()
