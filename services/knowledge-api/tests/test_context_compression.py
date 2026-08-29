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


def test_complete_table_group_keeps_all_sixteen_matching_rows() -> None:
    cities = ["杭州", "成都", "苏州", "南京", "武汉", "西安", "天津", "重庆", "郑州", "长沙", "青岛", "宁波", "东莞", "合肥", "佛山", "沈阳"]
    rows = "\n".join(f"|新一线城市|{city}|" for city in cities)
    item = {
        "document_name": "城市分级.xlsx",
        "heading": "城市分级",
        "content": "|城市等级|城市名称|\n|新一线城市|杭州|",
        "parent_content": f"|城市等级|城市名称|\n|---|---|\n|一线城市|北京|\n{rows}\n|二线城市|厦门|",
    }

    compacted = compact_evidence_text("新一线城市是哪16座？", item, 700)

    assert all(city in compacted for city in cities)
    assert "北京" not in compacted
    assert "厦门" not in compacted
    assert compacted.count("新一线城市") == 16


def test_complete_list_gets_a_larger_first_evidence_budget() -> None:
    cities = [f"城市{index:02d}" for index in range(1, 17)]
    rows = "\n".join(f"|新一线城市|{city}|" for city in cities)
    evidence = [{
        "document_name": "城市分级.xlsx",
        "heading": "城市分级",
        "content": "|新一线城市|城市01|",
        "parent_content": f"|等级|城市|\n|---|---|\n{rows}",
    }] + [{
        "document_name": f"旁证{index}.txt",
        "heading": "正文",
        "content": "新一线城市说明。",
        "parent_content": "新一线城市说明。" * 30,
    } for index in range(5)]

    context = compact_evidence_context("新一线城市有哪些？", evidence, max_chars=1800)

    assert all(city in context for city in cities)
    assert len(context) <= 1800


def test_rule_overview_keeps_all_conditions_from_one_policy_paragraph() -> None:
    item = {
        "document_name": "考勤制度.docx",
        "heading": "迟到",
        "content": "迟到不足15分钟（含）的，当月第一次口头提醒，不扣款。",
        "parent_content": (
            "第八条 迟到\n"
            "迟到不足15分钟（含）的，当月第一次口头提醒，不扣款；当月第二次起每次扣款30元。"
            "迟到超过15分钟不足30分钟的，每次扣款50元。迟到超过30分钟不足2小时的，按旷工半天处理。"
            "迟到2小时及以上的，按旷工一天处理。自然月内迟到累计达到3次的，加记旷工半天。"
        ),
    }

    compacted = compact_evidence_text("迟到问题是怎么规定的？", item, 420)

    assert "第二次起每次扣款30元" in compacted
    assert "超过30分钟不足2小时" in compacted
    assert "2小时及以上" in compacted
    assert "累计达到3次" in compacted


def test_salary_date_is_answered_from_clear_policy_sentence() -> None:
    evidence = [{
        "content": "工资于每月15日发放。",
        "parent_content": "工资于每月15日发放。如遇节假日或公休日，提前至最近工作日发放。",
    }]

    answer = fast_factual_answer("工资哪天发？", evidence)

    assert answer is not None
    assert "15 日" in answer
    assert "提前至最近工作日" in answer
    assert "[[1]]" in answer


def test_half_year_tenure_is_below_leave_eligibility_threshold() -> None:
    evidence = [{
        "content": "员工连续工作满1年后开始享受年假。",
        "parent_content": "员工连续工作满1年后开始享受年假。",
    }]

    answer = fast_factual_answer("我入职半年，年假有几天？", evidence)

    assert answer is not None
    assert "0 天" in answer
    assert "满 1年" in answer


def test_allowance_difference_is_calculated_from_two_table_rows() -> None:
    evidence = [{
        "content": "|职级|通讯补贴|\n|经理|200元|\n|总监|400元|",
        "parent_content": "|职级|通讯补贴|\n|---|---|\n|经理|200元|\n|总监|400元|",
    }]

    answer = fast_factual_answer("经理和总监通讯补贴差多少？", evidence)

    assert answer is not None
    assert "经理" in answer and "200 元" in answer
    assert "总监" in answer and "400 元" in answer
    assert "相差 **200 元**" in answer


def test_allowance_difference_maps_values_through_pivot_table_header() -> None:
    evidence = [{
        "content": "|项目|普通员工|经理|总监|\n|通讯补贴|100元|200元|400元|",
        "parent_content": "|项目|P1-P4普通员工|P5-P6经理|P7-P8总监|\n|---|---|---|---|\n|交通补贴|300元|500元|800元|\n|通讯补贴|100元|200元|400元|",
    }]

    answer = fast_factual_answer("经理和总监通讯补贴差多少？", evidence)

    assert answer is not None
    assert "200 元" in answer and "400 元" in answer
    assert "相差 **200 元**" in answer


def test_threshold_consequence_applies_input_amount_to_policy_limit() -> None:
    evidence = [{
        "content": "虚假报销应追回全额并记大过。金额超过5000元，解除劳动合同并移送司法机关。",
        "parent_content": "虚假报销一经发现，追回全额并记大过。金额超过5000元，解除劳动合同并移送司法机关。",
    }]

    answer = fast_factual_answer("虚假报销6000元怎么处理？", evidence)

    assert answer is not None
    assert "6000 元" in answer and "5000 元" in answer
    assert "追回全额" in answer and "记大过" in answer
    assert "解除劳动合同" in answer and "移送司法机关" in answer


def test_threshold_consequence_survives_pdf_hard_line_wrap() -> None:
    evidence = [{
        "content": "虚假发票、重复报销的，追回全额费用，记大过，金额超过",
        "parent_content": "虚假发票、重复报销的，追回全额费用，记大过，金额超过\n5000元的，解除劳动合同并移送司法机关。",
    }]

    answer = fast_factual_answer("虚假报销6000元怎么处理？", evidence)

    assert answer is not None
    assert "追回全额" in answer and "记大过" in answer
    assert "解除劳动合同" in answer and "移送司法机关" in answer


def test_expense_cap_binds_city_category_and_role_before_comparison() -> None:
    evidence = [
        {
            "content": "|一线|450|600|800|北京、上海、广州、深圳|",
            "parent_content": "|城市类别|P1-P4普通员工|P5-P6经理|P7-P8总监|城市范围|\n|---|---|---|---|---|\n|一线|450|600|800|北京、上海、广州、深圳|\n|二线及以下|350|500|650|其他国内城市|",
        },
        {
            "content": "会议或客户指定酒店超过标准的，须在出差申请中注明并经分管副总",
            "parent_content": "会议或客户\n指定酒店超过标准的，须在出差申请中注明并经分管副总同意。",
        },
    ]

    answer = fast_factual_answer("经理去深圳住酒店650元/晚能不能报？", evidence)

    assert answer is not None
    assert "不能按 **650 元/晚**全额报销" in answer
    assert "一线城市" in answer and "600 元/晚" in answer
    assert "50 元/晚" in answer
    assert "指定酒店" in answer and "分管副总同意" in answer
    assert "P7-P8" not in answer
