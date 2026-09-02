from __future__ import annotations

from knowledge_api.retrieval_quality import assess_retrieval, validate_query_expansion


def test_retrieval_assessment_keeps_answer_threshold_unchanged() -> None:
    sufficient = assess_retrieval([{"evidence_score": 0.45, "vector_score": 0.4}])
    recoverable = assess_retrieval([{"evidence_score": 0.327, "vector_score": 0.7541}])
    unsupported = assess_retrieval([{"evidence_score": 0.12, "vector_score": 0.18}])

    assert sufficient.confidence == "sufficient"
    assert recoverable.confidence == "recoverable"
    assert unsupported.confidence == "unsupported"


def test_query_expansion_preserves_original_constraints_and_rejects_drift() -> None:
    validated = validate_query_expansion(
        "离职后工资什么时候发？",
        ["离职员工工资发放日期", "工资发放日期", "奖金通常什么时候发"],
        ["离职", "工资"],
        [0.82, 0.79, 0.41],
    )

    assert validated.accepted == ("离职员工工资发放日期",)
    assert [item["reason"] for item in validated.rejected] == ["constraint-loss", "semantic-drift"]
    assert validated.drift_detected is True


def test_query_expansion_preserves_numeric_and_identifier_anchors() -> None:
    validated = validate_query_expansion(
        "设备 ZY-7788 保留多少天？",
        ["ZY-7788 设备保留期限", "设备保留期限"],
        ["设备"],
        [0.88, 0.81],
    )

    assert validated.accepted == ("ZY-7788 设备保留期限",)
    assert validated.rejected[0]["reason"] == "anchor-loss"
