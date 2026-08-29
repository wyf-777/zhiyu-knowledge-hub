from __future__ import annotations

from pathlib import Path

import pytest

from knowledge_api.evaluation import load_dataset, ndcg_at_k, reciprocal_rank, recall_at_k, run_retrieval_evaluation, validate_corpus_provenance


def test_retrieval_metric_calculations() -> None:
    returned = ["other.md", "target.md", "second.md"]
    relevant = {"target.md", "second.md"}

    assert recall_at_k(returned, relevant, 2) == 0.5
    assert reciprocal_rank(returned, relevant) == 0.5
    assert 0 < ndcg_at_k(returned, relevant, 3) < 1


def test_versioned_retrieval_baseline(tmp_path: Path) -> None:
    dataset_path = Path(__file__).resolve().parents[1] / "evaluation" / "retrieval_baseline.json"
    report = run_retrieval_evaluation(load_dataset(dataset_path), tmp_path, top_k=5)

    assert report["dataset_version"] == "zhiyu-retrieval-v1"
    assert report["provenance"]["type"] == "synthetic"
    assert report["case_count"] == 11
    assert report["metrics"]["recall_at_k"] >= 0.95
    assert report["metrics"]["mrr"] >= 0.95
    assert report["metrics"]["answerability_accuracy"] >= 0.9


def test_realistic_retrieval_baseline_includes_chunk_metrics(tmp_path: Path) -> None:
    dataset_path = Path(__file__).resolve().parents[1] / "evaluation" / "retrieval_realistic_v2.json"
    report = run_retrieval_evaluation(load_dataset(dataset_path), tmp_path, top_k=5)

    assert report["dataset_version"] == "zhiyu-retrieval-v2"
    assert report["case_count"] == 22
    assert report["metrics"]["recall_at_k"] >= 0.9
    assert report["metrics"]["chunk_recall_at_k"] >= 0.85
    assert report["metrics"]["answerability_accuracy"] >= 0.85


def test_experience_regression_manifest_contains_no_document_body() -> None:
    dataset_path = Path(__file__).resolve().parents[1] / "evaluation" / "experience_regression_v1.json"
    payload = load_dataset(dataset_path)

    assert payload["version"] == "zhiyu-experience-regression-v1"
    assert payload["provenance"]["contains_document_content"] is False
    assert "documents" not in payload
    assert len(payload["cases"]) == 9


def test_real_corpus_gate_rejects_synthetic_and_accepts_reviewed_sanitized_data() -> None:
    with pytest.raises(ValueError, match="不是经脱敏企业语料"):
        validate_corpus_provenance(
            {"provenance": {"type": "synthetic", "organization": "anonymous", "reviewed_at": ""}},
            require_real_corpus=True,
        )

    provenance = validate_corpus_provenance(
        {
            "provenance": {
                "type": "sanitized-enterprise",
                "organization": "anonymous",
                "reviewed_at": "2026-08-29",
            }
        },
        require_real_corpus=True,
    )
    assert provenance["type"] == "sanitized-enterprise"
