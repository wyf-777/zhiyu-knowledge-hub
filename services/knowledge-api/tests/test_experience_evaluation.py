from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from knowledge_api.experience_evaluation import snapshot_knowledge_data, validate_experience_manifest


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
