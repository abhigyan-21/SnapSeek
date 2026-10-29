"""Tests for semantic ranking using deterministic fake embeddings."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from snapseek.scanner import ImageMetadata
from snapseek.search import OCRTextRecord
from snapseek.semantic import (
    MODEL_NAME,
    SemanticDependencyError,
    SemanticInferenceError,
    SemanticModelError,
    SemanticSearchResult,
    load_semantic_encoder,
    search_semantic,
)


class FakeEncoder:
    def __init__(self, document_vectors, query_vector=(1.0, 0.0)) -> None:
        self.document_vectors = document_vectors
        self.query_vector = query_vector
        self.document_calls = []
        self.query_calls = []

    def embed(self, documents):
        self.document_calls.append(list(documents))
        return iter(self.document_vectors)

    def query_embed(self, query):
        self.query_calls.append(query)
        return iter([self.query_vector])


class SemanticSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.near = OCRTextRecord(
            ImageMetadata("near.png", root / "near.png", ".png", 1),
            "A message about a failed payment",
        )
        self.far = OCRTextRecord(
            ImageMetadata("far.png", root / "far.png", ".png", 2),
            "A reminder about the weather",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ranks_by_cosine_and_preserves_record_association(self) -> None:
        encoder = FakeEncoder([(0.8, 0.6), (0.0, 1.0)])

        results = search_semantic("payment issue", [self.near, self.far], encoder=encoder)

        self.assertEqual([result.record for result in results], [self.near, self.far])
        self.assertAlmostEqual(results[0].score, 0.8)
        self.assertAlmostEqual(results[1].score, 0.0)
        self.assertEqual(results[0].record.image.path, self.near.image.path)
        self.assertEqual(results[0].record.ocr_text, self.near.ocr_text)
        self.assertTrue(all(isinstance(result, SemanticSearchResult) for result in results))
        self.assertEqual(encoder.document_calls, [[self.near.ocr_text, self.far.ocr_text]])
        self.assertEqual(encoder.query_calls, ["payment issue"])

    def test_returns_all_nonempty_documents_without_a_score_threshold(self) -> None:
        encoder = FakeEncoder([(1.0, 0.0), (-0.2, 0.98)])

        results = search_semantic("unrelated", [self.near, self.far], encoder=encoder)

        self.assertEqual(len(results), 2)
        self.assertLess(results[1].score, 0.0)

    def test_tied_scores_use_normalized_posix_path_order(self) -> None:
        root = Path(self.temp_dir.name)
        z_record = OCRTextRecord(
            ImageMetadata("z.png", root / "z.png", ".png", 1), "same text"
        )
        a_record = OCRTextRecord(
            ImageMetadata("A.png", root / "A.png", ".png", 1), "same text"
        )

        results = search_semantic(
            "query", [z_record, a_record], encoder=FakeEncoder([(0.5, 0.5)] * 2)
        )

        self.assertEqual([result.record for result in results], [a_record, z_record])
        self.assertEqual(results[0].score, results[1].score)

    def test_empty_collection_returns_without_loading_or_using_model(self) -> None:
        with patch("snapseek.semantic.load_semantic_encoder") as load_mock:
            self.assertEqual(search_semantic("query", []), [])
        load_mock.assert_not_called()

    def test_empty_and_whitespace_documents_are_skipped(self) -> None:
        empty = OCRTextRecord(self.near.image, " \t\n")
        encoder = FakeEncoder([(0.8, 0.6)])

        results = search_semantic("query", [empty, self.far], encoder=encoder)

        self.assertEqual([result.record for result in results], [self.far])
        self.assertEqual(encoder.document_calls, [[self.far.ocr_text]])

    def test_all_empty_documents_return_without_loading_model(self) -> None:
        empty = OCRTextRecord(self.near.image, "  ")
        with patch("snapseek.semantic.load_semantic_encoder") as load_mock:
            self.assertEqual(search_semantic("query", [empty]), [])
        load_mock.assert_not_called()

    def test_blank_query_is_rejected(self) -> None:
        for query in ("", " \t\n"):
            with self.subTest(query=repr(query)):
                with self.assertRaisesRegex(ValueError, "must not be empty"):
                    search_semantic(query, [self.near], encoder=FakeEncoder([]))

    def test_missing_fastembed_dependency_is_actionable(self) -> None:
        missing_package = ModuleNotFoundError("No module named 'fastembed'", name="fastembed")
        with patch("snapseek.semantic.import_module", side_effect=missing_package):
            with self.assertRaisesRegex(SemanticDependencyError, "pip install -e") as error:
                load_semantic_encoder()
        self.assertIs(error.exception.__cause__, missing_package)

    def test_model_initialization_failure_is_actionable_and_chained(self) -> None:
        network_error = OSError("network unavailable")
        fake_module = type(
            "FakeFastEmbed",
            (),
            {"TextEmbedding": staticmethod(lambda **kwargs: (_ for _ in ()).throw(network_error))},
        )
        with patch("snapseek.semantic.import_module", return_value=fake_module):
            with self.assertRaisesRegex(SemanticModelError, "first download") as error:
                load_semantic_encoder()
        self.assertIs(error.exception.__cause__, network_error)

    def test_unknown_model_initialization_programming_error_is_not_wrapped(self) -> None:
        fake_module = type(
            "FakeFastEmbed",
            (),
            {"TextEmbedding": staticmethod(lambda **kwargs: (_ for _ in ()).throw(ValueError("bug")))},
        )
        with patch("snapseek.semantic.import_module", return_value=fake_module):
            with self.assertRaisesRegex(ValueError, "bug"):
                load_semantic_encoder()

    def test_inference_runtime_failure_is_wrapped_and_chained(self) -> None:
        failure = RuntimeError("encoder stopped")
        encoder = FakeEncoder([])
        encoder.embed = lambda documents: (_ for _ in ()).throw(failure)

        with self.assertRaisesRegex(SemanticInferenceError, "inference failed") as error:
            search_semantic("query", [self.near], encoder=encoder)
        self.assertIs(error.exception.__cause__, failure)

    def test_unrelated_inference_programming_error_is_not_wrapped(self) -> None:
        encoder = FakeEncoder([])
        encoder.embed = lambda documents: (_ for _ in ()).throw(TypeError("bug"))

        with self.assertRaisesRegex(TypeError, "bug"):
            search_semantic("query", [self.near], encoder=encoder)

    def test_invalid_vector_dimensions_are_reported(self) -> None:
        encoder = FakeEncoder([(1.0, 0.0), (0.0, 1.0)], query_vector=(1.0,))

        with self.assertRaisesRegex(SemanticInferenceError, "different dimensions"):
            search_semantic("query", [self.near, self.far], encoder=encoder)

    def test_load_uses_the_approved_model_name(self) -> None:
        fake_encoder = FakeEncoder([])
        constructor = Mock(return_value=fake_encoder)
        fake_module = SimpleNamespace(TextEmbedding=constructor)
        with patch("snapseek.semantic.import_module", return_value=fake_module):
            self.assertIs(load_semantic_encoder(), fake_encoder)
        constructor.assert_called_once_with(model_name=MODEL_NAME)


if __name__ == "__main__":
    unittest.main()
