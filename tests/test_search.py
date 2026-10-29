"""Tests for in-memory keyword and TF-IDF search over OCR records."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from snapseek.scanner import ImageMetadata
from snapseek.search import (
    OCRTextRecord,
    RankedSearchResult,
    SearchDependencyError,
    search_keyword,
    search_tfidf,
)


class SearchKeywordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.first = OCRTextRecord(
            ImageMetadata("first.png", root / "first.png", ".png", 1),
            "Payment failed while saving",
        )
        self.second = OCRTextRecord(
            ImageMetadata("second.png", root / "second.png", ".png", 2),
            "A PAYMENT FAILED message appeared",
        )
        self.third = OCRTextRecord(
            ImageMetadata("third.png", root / "third.png", ".png", 3),
            "Nothing relevant here",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_matching_is_case_insensitive_and_preserves_association(self) -> None:
        matches = search_keyword("PAYMENT", [self.first, self.second, self.third])

        self.assertEqual(matches, [self.first, self.second])
        self.assertEqual(matches[0].image.path.name, "first.png")
        self.assertEqual(matches[0].ocr_text, "Payment failed while saving")

    def test_phrase_uses_substring_matching(self) -> None:
        self.assertEqual(
            search_keyword("payment failed", [self.third, self.second]),
            [self.second],
        )

    def test_multiple_matches_keep_input_order(self) -> None:
        self.assertEqual(
            search_keyword("failed", [self.second, self.first]),
            [self.second, self.first],
        )

    def test_no_matches_returns_empty_list(self) -> None:
        self.assertEqual(search_keyword("invoice", [self.first, self.third]), [])

    def test_empty_and_whitespace_queries_are_rejected(self) -> None:
        for query in ("", "   ", "\t\n"):
            with self.subTest(query=repr(query)):
                with self.assertRaisesRegex(ValueError, "must not be empty"):
                    search_keyword(query, [self.first])

    def test_empty_ocr_text_does_not_match_nonempty_query(self) -> None:
        empty = OCRTextRecord(self.third.image, "")
        self.assertEqual(search_keyword("anything", [empty]), [])

    def test_tfidf_ranks_more_relevant_records_and_preserves_association(self) -> None:
        detailed = OCRTextRecord(
            self.first.image,
            "payment failed transaction payment failed",
        )
        partial = OCRTextRecord(self.second.image, "payment completed")
        unrelated = OCRTextRecord(self.third.image, "calendar reminder")

        results = search_tfidf("payment failed", [partial, unrelated, detailed])

        self.assertEqual([result.record for result in results], [detailed, partial])
        self.assertGreater(results[0].score, results[1].score)
        self.assertGreater(results[1].score, 0.0)
        self.assertEqual(results[0].record.image.path, self.first.image.path)
        self.assertEqual(
            results[0].record.ocr_text,
            "payment failed transaction payment failed",
        )
        self.assertTrue(all(isinstance(result, RankedSearchResult) for result in results))

    def test_tfidf_ties_are_broken_by_normalized_image_path(self) -> None:
        z_image = OCRTextRecord(
            ImageMetadata("z.png", self.first.image.path.parent / "z.png", ".png", 1),
            "same words here",
        )
        a_image = OCRTextRecord(
            ImageMetadata("A.png", self.first.image.path.parent / "A.png", ".png", 1),
            "same words here",
        )

        results = search_tfidf("same words", [z_image, a_image])

        self.assertEqual([result.record for result in results], [a_image, z_image])
        self.assertEqual(results[0].score, results[1].score)

    def test_tfidf_excludes_zero_score_records(self) -> None:
        matching = OCRTextRecord(self.first.image, "invoice payment")
        unrelated = OCRTextRecord(self.second.image, "calendar reminder")

        results = search_tfidf("payment", [matching, unrelated])

        self.assertEqual([result.record for result in results], [matching])
        self.assertGreater(results[0].score, 0.0)

    def test_tfidf_empty_collection_returns_no_results(self) -> None:
        self.assertEqual(search_tfidf("payment", []), [])

    def test_tfidf_empty_documents_are_excluded_without_losing_other_matches(self) -> None:
        empty = OCRTextRecord(self.first.image, "")
        matching = OCRTextRecord(self.second.image, "payment failed")

        results = search_tfidf("payment", [empty, matching])

        self.assertEqual([result.record for result in results], [matching])

    def test_tfidf_all_unusable_documents_return_no_results(self) -> None:
        unusable = [
            OCRTextRecord(self.first.image, ""),
            OCRTextRecord(self.second.image, "!? 7 x"),
        ]

        self.assertEqual(search_tfidf("payment", unusable), [])

    def test_tfidf_query_without_vocabulary_overlap_returns_no_results(self) -> None:
        results = search_tfidf("unknown", [self.first, self.second])

        self.assertEqual(results, [])
        self.assertEqual(search_tfidf("!? x", [self.first, self.second]), [])

    def test_tfidf_blank_query_is_rejected(self) -> None:
        for query in ("", "  \t"):
            with self.subTest(query=repr(query)):
                with self.assertRaisesRegex(ValueError, "must not be empty"):
                    search_tfidf(query, [self.first])

    def test_missing_sklearn_is_actionable_and_keyword_search_still_works(self) -> None:
        missing_package = ModuleNotFoundError(
            "No module named 'sklearn'", name="sklearn"
        )
        with patch("snapseek.search.import_module", side_effect=missing_package):
            with self.assertRaisesRegex(SearchDependencyError, "scikit-learn") as error:
                search_tfidf("Payment", [self.first])

            self.assertIs(error.exception.__cause__, missing_package)
            self.assertEqual(search_keyword("PAYMENT", [self.first]), [self.first])


if __name__ == "__main__":
    unittest.main()
