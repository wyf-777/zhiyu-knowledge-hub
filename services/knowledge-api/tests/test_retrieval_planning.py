from __future__ import annotations

from knowledge_api.retrieval_planning import alternate_questions, build_retrieval_queries


def test_multi_slot_question_is_split_and_inherits_subject() -> None:
    queries = build_retrieval_queries("差旅平台找谁开通？分机多少？入职几天能开？")

    assert "差旅平台 分机多少" in queries
    assert "差旅平台 入职几天能开" in queries
    assert len(queries) <= 5


def test_permission_question_adds_rule_and_exception_vocabulary() -> None:
    queries = build_retrieval_queries("私人U盘能拷文件吗？")

    assert any("是否允许" in query and "条件" in query and "禁止" in query for query in queries)
    assert any("复制文件" in query and "加密U盘" in query for query in queries)


def test_range_lookup_adds_bracket_query_without_input_number() -> None:
    queries = build_retrieval_queries("司龄6年的年假有几天？")

    assert any("档位" in query and "6" not in query for query in queries)


def test_no_evidence_suggestions_are_clickable_questions_not_ui_templates() -> None:
    suggestions = alternate_questions("如何办理访客权限？")

    assert suggestions
    assert "换一种方式提问" not in suggestions
    assert "查看已上传文档" not in suggestions
