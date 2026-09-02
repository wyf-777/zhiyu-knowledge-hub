from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from knowledge_api.experience_evaluation import (
    build_expression_metrics,
    evaluate_quality_gates,
    load_experience_manifest,
    snapshot_knowledge_data,
    validate_experience_manifest,
)


def test_v3_experience_manifest_is_content_free_and_unique() -> None:
    manifest_path = Path(__file__).resolve().parents[1] / "evaluation" / "experience_regression_v3.json"
    payload = validate_experience_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))

    assert payload["version"] == "zhiyu-experience-regression-v3"
    assert payload["provenance"]["contains_document_content"] is False
    assert "documents" not in payload
    assert len(payload["cases"]) == 22
    assert len({case["id"] for case in payload["cases"]}) == 22


def test_experience_manifest_rejects_embedded_documents() -> None:
    with pytest.raises(ValueError, match="不得包含原始文档正文"):
        validate_experience_manifest({
            "provenance": {"type": "local-user-feedback", "contains_document_content": False},
            "documents": [{"content": "private"}],
            "cases": [{"id": "one", "query": "question"}],
        })


def test_v4_manifest_extends_real_cases_with_expression_groups() -> None:
    manifest_path = Path(__file__).resolve().parents[1] / "evaluation" / "experience_regression_v4.json"

    payload = load_experience_manifest(manifest_path)

    grouped = [case for case in payload["cases"] if case.get("paraphrase_group_id") == "salary-payment-date"]
    assert payload["version"] == "zhiyu-experience-regression-v4"
    assert len(payload["cases"]) == 30
    assert len([case for case in grouped if case["variant_kind"] == "equivalent"]) == 5
    assert len([case for case in grouped if case["variant_kind"] == "contrast"]) == 3
    assert any(case["query"] == "工资什么时候发？" for case in grouped)


def test_expression_metrics_detect_source_instability_and_query_drift() -> None:
    reports = [
        {
            "paraphrase_group_id": "salary-date",
            "variant_kind": "equivalent",
            "retrieval_passed": True,
            "answer_passed": True,
            "matched_expected_sources": ["salary.md", "handbook.docx"],
            "query_drift": False,
        },
        {
            "paraphrase_group_id": "salary-date",
            "variant_kind": "equivalent",
            "retrieval_passed": False,
            "answer_passed": False,
            "matched_expected_sources": ["salary.md"],
            "query_drift": False,
        },
        {
            "paraphrase_group_id": "salary-date",
            "variant_kind": "contrast",
            "retrieval_passed": False,
            "answer_passed": False,
            "matched_expected_sources": [],
            "query_drift": True,
        },
    ]

    metrics = build_expression_metrics(reports)
    failures = evaluate_quality_gates(metrics, {
        "min_expression_consistency_rate": 1.0,
        "min_source_consistency_rate": 0.8,
        "min_contrast_accuracy_rate": 1.0,
        "min_answer_consistency_rate": 1.0,
        "min_contrast_answer_accuracy_rate": 1.0,
        "max_query_drift_rate": 0.0,
    })

    assert metrics["expression_consistency_rate"] == 0.5
    assert metrics["source_consistency_rate"] == 0.5
    assert metrics["query_drift_rate"] == pytest.approx(1 / 3, abs=0.0001)
    assert metrics["contrast_accuracy_rate"] == 0.0
    assert metrics["answer_consistency_rate"] == 0.5
    assert metrics["contrast_answer_accuracy_rate"] == 0.0
    assert len(failures) == 6


def test_expression_group_requires_explicit_variant_kind() -> None:
    with pytest.raises(ValueError, match="variant_kind"):
        validate_experience_manifest({
            "provenance": {"type": "local-user-feedback", "contains_document_content": False},
            "cases": [{"id": "one", "query": "工资哪天发", "paraphrase_group_id": "salary"}],
        })


def test_snapshot_reads_source_database_without_modifying_it(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source_database = source_dir / "knowledge.db"
    with sqlite3.connect(source_database) as connection:
        connection.execute("CREATE TABLE sample(value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample(value) VALUES ('kept')")
    before = hashlib.sha256(source_database.read_bytes()).hexdigest()

    snapshot_database = snapshot_knowledge_data(source_dir, tmp_path / "snapshot")

    after = hashlib.sha256(source_database.read_bytes()).hexdigest()
    with sqlite3.connect(snapshot_database) as connection:
        value = connection.execute("SELECT value FROM sample").fetchone()[0]
    assert before == after
    assert value == "kept"
