from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    "question",
    ["工资哪天发？", "什么时候发工资？", "工资什么时候发？", "发薪日期是什么时候？"],
)
def test_payment_time_grammar_normalization_handles_word_order(question: str) -> None:
    queries = build_retrieval_queries(question)

    assert queries[0] == question.rstrip("？")
    assert any("时间" in query and "日期" in query and "发薪日" in query for query in queries[1:])


@pytest.mark.parametrize("subject", ["离职后工资", "奖金", "加班费", "报销款"])
def test_payment_time_normalization_preserves_the_asked_subject(subject: str) -> None:
    queries = build_retrieval_queries(f"{subject}什么时候发？")

    assert all(subject in query for query in queries)


def test_half_year_leave_question_adds_eligibility_bracket_vocabulary() -> None:
    queries = build_retrieval_queries("我入职半年，年假有几天？")

    assert any("连续工作满1年" in query and "年假" in query for query in queries)


def test_annual_leave_typo_adds_narrow_corrected_retrieval_route() -> None:
    queries = build_retrieval_queries("年假没有修完可以换成工资吗？")

    assert queries[0] == "年假没有修完可以换成工资吗"
    assert "年假没有休完可以换成工资吗" in queries


def test_unrelated_repair_word_is_not_silently_corrected() -> None:
    queries = build_retrieval_queries("电脑没有修完可以领备用机吗？")

    assert not any("休完" in query for query in queries)


def test_false_expense_claim_adds_violation_synonyms() -> None:
    queries = build_retrieval_queries("虚假报销6000元怎么处理？")

    assert any("虚假发票" in query and "重复报销" in query for query in queries)


def test_allowance_difference_adds_standard_and_difference_terms() -> None:
    queries = build_retrieval_queries("经理和总监通讯补贴差多少？")

    assert any("补贴标准" in query and "差额" in query for query in queries)


def test_late_duration_adds_attendance_and_range_vocabulary() -> None:
    queries = build_retrieval_queries("迟到20分钟怎么算？")

    assert any("考勤迟到" in query and "处罚档位" in query for query in queries)
    assert any("15分钟" in query and "30分钟" in query for query in queries)
