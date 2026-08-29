from __future__ import annotations

from knowledge_api.answering import fast_factual_answer
from knowledge_api.context_compression import compact_evidence_context, compact_evidence_text


def test_table_context_keeps_matching_row_without_markdown_scaffolding() -> None:
    item = {
        "document_name": "考勤表.xls",
        "heading": "各地工作时间",
        "content": "|地点|工作时间|午休|\n|杭州总部|9:00-18:00|12:00-13:00|",
        "parent_content": (
            "# 全公司考勤\n|地点|工作时间|午休|\n|---|---|---|\n"
            "|北京办事处|8:30-17:30|12:00-13:00|\n|杭州总部|9:00-18:00|12:00-13:00|\n"
            "|深圳办事处|9:30-18:30|12:30-13:30|"
        ),
    }

    compacted = compact_evidence_text("杭州总部上班时间？", item, 220)

    assert "杭州总部" in compacted
    assert "9:00-18:00" in compacted
    assert "|---|" not in compacted
    assert not compacted.startswith("#")


def test_total_answer_context_stays_inside_strict_budget() -> None:
    evidence = [
        {
            "document_name": f"制度{index}.txt",
            "heading": "正文",
            "content": "杭州总部工作时间为9:00-18:00。" * 30,
            "parent_content": "无关制度说明。" * 500 + "杭州总部工作时间为9:00-18:00。",
        }
        for index in range(6)
    ]

    context = compact_evidence_context("杭州总部上班时间？", evidence, max_chars=1800)

    assert len(context) <= 1800
    assert "杭州总部" in context


def test_high_confidence_work_time_lookup_is_answered_without_llm() -> None:
    evidence = [{
        "document_name": "考勤表.xls",
        "heading": "各地工作时间",
        "content": "杭州总部 / 9:00 / 18:00 / 12:00 / 13:00",
        "parent_content": "杭州总部 / 9:00 / 18:00 / 12:00 / 13:00",
    }]

    answer = fast_factual_answer("杭州总部上班时间？", evidence)

    assert answer == "杭州总部的工作时间为 **9:00–18:00**，午休时间为 **12:00–13:00**。 [[1]]"
