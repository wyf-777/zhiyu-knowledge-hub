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


def test_salary_date_paraphrase_adds_policy_vocabulary() -> None:
    queries = build_retrieval_queries("工资哪天发？")

    assert any("发薪日" in query and "工资发放日期" in query for query in queries)


def test_half_year_leave_question_adds_eligibility_bracket_vocabulary() -> None:
    queries = build_retrieval_queries("我入职半年，年假有几天？")

    assert any("连续工作满1年" in query and "年假" in query for query in queries)


def test_false_expense_claim_adds_violation_synonyms() -> None:
    queries = build_retrieval_queries("虚假报销6000元怎么处理？")

    assert any("虚假发票" in query and "重复报销" in query for query in queries)


def test_allowance_difference_adds_standard_and_difference_terms() -> None:
    queries = build_retrieval_queries("经理和总监通讯补贴差多少？")

    assert any("补贴标准" in query and "差额" in query for query in queries)
