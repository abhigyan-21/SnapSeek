"""OCR generated screenshots and compare the three retrieval approaches."""

import argparse
from importlib import metadata
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from snapseek.evaluation import (  # noqa: E402
    APPROACHES,
    EvaluationDataError,
    evaluate_records,
    load_label_manifest,
    macro_averages,
)
from snapseek.ocr import OCRError, extract_text  # noqa: E402
from snapseek.scanner import scan_images  # noqa: E402
from snapseek.search import OCRTextRecord, SearchDependencyError  # noqa: E402
from snapseek.semantic import MODEL_NAME, SemanticSearchError, load_semantic_encoder  # noqa: E402
from generate_synthetic_dataset import SCREENS  # noqa: E402


DEFAULT_DATASET = ROOT / "evaluation" / "generated"
DEFAULT_MANIFEST = ROOT / "evaluation" / "queries.json"


def _version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "not installed"


def run(dataset_dir: Path, manifest_path: Path) -> int:
    """Run the live evaluation or fail before printing partial metrics."""
    expected_ids = {screen[0] for screen in SCREENS}
    try:
        images = scan_images(dataset_dir)
        actual_ids = [image.path.stem for image in images]
        if len(set(actual_ids)) != len(actual_ids):
            raise EvaluationDataError("Generated screenshot IDs must be unique.")
        if set(actual_ids) != expected_ids:
            missing = sorted(expected_ids - set(actual_ids))
            extra = sorted(set(actual_ids) - expected_ids)
            raise EvaluationDataError(
                f"Dataset mismatch. Missing: {missing or 'none'}; extra: {extra or 'none'}. "
                "Regenerate it with scripts/generate_synthetic_dataset.py."
            )
        queries = load_label_manifest(manifest_path, expected_ids)

        records = []
        for image in images:
            text = extract_text(image)
            if not text.strip():
                raise EvaluationDataError(
                    f"OCR returned no text for expected screenshot {image.path.name}."
                )
            records.append(OCRTextRecord(image=image, ocr_text=text))

        encoder = load_semantic_encoder()
        evaluations = evaluate_records(records, queries, encoder=encoder)
        averages = macro_averages(evaluations)
        tesseract_version = str(__import__("pytesseract").get_tesseract_version())
    except (
        OSError,
        OCRError,
        SearchDependencyError,
        SemanticSearchError,
        EvaluationDataError,
    ) as error:
        print(f"Evaluation failed; no partial metrics were produced: {error}", file=sys.stderr)
        print(
            "Check the generated dataset, Tesseract installation, Python dependencies, "
            "and FastEmbed model availability.",
            file=sys.stderr,
        )
        return 1

    print("SnapSeek synthetic retrieval evaluation")
    print(f"Python: {sys.version.split()[0]}")
    print(f"pytesseract: {_version('pytesseract')}; Tesseract: {tesseract_version}")
    print(f"scikit-learn: {_version('scikit-learn')}")
    print(f"FastEmbed: {_version('fastembed')}; ONNX Runtime: {_version('onnxruntime')}")
    print(f"Semantic model: {MODEL_NAME}")
    print(f"Dataset: {len(records)} screenshots; queries: {len(queries)}")
    print("\nPer-query metrics (P=precision, R=recall):")
    print("query_id,approach,P@5,R@5,P@10,R@10")
    for row in evaluations:
        p5, r5 = row.metrics[5].precision, row.metrics[5].recall
        p10, r10 = row.metrics[10].precision, row.metrics[10].recall
        print(f"{row.query_id},{row.approach},{p5:.3f},{r5:.3f},{p10:.3f},{r10:.3f}")

    print("\nUnweighted macro-averages:")
    print("approach,P@5,R@5,P@10,R@10")
    for approach in APPROACHES:
        scores = averages[approach]
        print(
            f"{approach},{scores[5].precision:.3f},{scores[5].recall:.3f},"
            f"{scores[10].precision:.3f},{scores[10].recall:.3f}"
        )
    print(
        "\nKeyword matches are tied; screenshot ID order is a deterministic tie-break, "
        "not a relevance ranking."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    return run(args.dataset_dir, args.manifest)


if __name__ == "__main__":
    raise SystemExit(main())
