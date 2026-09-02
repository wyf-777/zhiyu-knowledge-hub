from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from .context_compression import compact_evidence_text
from .retrieval_planning import alternate_questions

MIN_EVIDENCE_SCORE = 0.45
MAX_EVIDENCE_ITEMS = 6

_TOPIC_EVIDENCE_RULES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"迟到"), ("迟到",)),
    (re.compile(r"年假|年休假"), ("年假", "年休假")),
    (re.compile(r"新一线城市"), ("新一线",)),
    (re.compile(r"虚假报销"), ("虚假报销", "虚假发票", "重复报销", "拆票", "私人消费冒充公务")),
    (re.compile(r"通讯补贴|通信补贴"), ("通讯补贴", "通信补贴", "话费补贴")),
    (re.compile(r"工资.*(?:发|哪天|几号)|(?:什么时候|哪天|几号).*发工资|发薪"), ("工资发放", "发薪", "工资")),
)

_CONDITIONAL_SALARY_CONTEXT = re.compile(
    r"离职|离岗|手续|交接|资产|电脑|设备|工卡|门禁卡|归还|不还|未还|借款|结清"
)


def _question_clauses(question: str) -> list[str]:
    return [part.strip(" ，,。！!") for part in re.split(r"[？?；;]+", question) if part.strip(" ，,。！!")]


def _is_multi_part_question(question: str) -> bool:
    """Return whether one turn contains multiple independently answerable slots."""

    return len(_question_clauses(question)) > 1


def evidence_score(item: dict[str, Any]) -> float:
    return float(item.get("evidence_score", item.get("score", 0.0)))


def filter_topic_consistent_results(question: str, search_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reject evidence from a clearly different explicit topic.

    This is intentionally a narrow safety gate. It activates only when exactly
    one explicit policy topic is present, so broad or multi-part questions keep
    the existing retrieval behavior.
    """

    if _is_multi_part_question(question):
        return search_results
    matched = [terms for pattern, terms in _TOPIC_EVIDENCE_RULES if pattern.search(question)]
    if len(matched) != 1:
        return search_results
    terms = matched[0]
    return [
        item
        for item in search_results
        if any(
            term in str(item.get(field) or "")
            for term in terms
            for field in ("document_name", "heading", "content", "parent_content")
        )
    ]


def select_evidence(question: str, search_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    complex_question = (
        len([part for part in re.split(r"[？?；;]+", question) if part.strip()]) > 1
        or bool(re.search(r"所有|全部|完整|逐一|罗列|列出|比较|分别", question))
    )
    limit = MAX_EVIDENCE_ITEMS if complex_question else 4
    return [item for item in search_results if evidence_score(item) >= MIN_EVIDENCE_SCORE][:limit]


def _evidence_units(item: dict[str, Any]) -> list[str]:
    text = str(item.get("parent_content") or item.get("content") or "")
    units: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"^#{1,6}\s*", "", raw_line.strip())
        if not line or re.fullmatch(r"\|?(?:\s*:?-{3,}:?\s*\|)+", line):
            continue
        if "|" in line:
            line = " / ".join(cell.strip() for cell in line.strip("|").split("|") if cell.strip())
        units.extend(
            sentence.strip(" -*")
            for sentence in re.split(r"(?<=[。！？；!?;])\s*", line)
            if sentence.strip(" -*")
        )
    return units


def _evidence_table_rows(item: dict[str, Any]) -> list[list[str]]:
    text = str(item.get("parent_content") or item.get("content") or "")
    rows: list[list[str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if "|" not in line or re.fullmatch(r"\|?(?:\s*:?-{3,}:?\s*\|)+", line):
            continue
        cells = [re.sub(r"\s+", " ", cell).strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 2 and any(cells):
            rows.append(cells)
    return rows


def _salary_date_answer(question: str, evidence: list[dict[str, Any]]) -> str | None:
    if not re.search(r"工资.*(?:哪天|几号|什么时候).*发|(?:哪天|几号|什么时候).*发工资|发薪日|工资发放日", question):
        return None
    # A generic pay-day lookup must never override a more specific departure,
    # handover or asset-return condition. Those turns need all clauses grounded
    # together by the answer model.
    if _CONDITIONAL_SALARY_CONTEXT.search(question):
        return None
    for index, item in enumerate(evidence, start=1):
        units = _evidence_units(item)
        for position, unit in enumerate(units):
            match = re.search(r"每月\s*(\d{1,2})\s*[日号]", unit)
            if not match or not re.search(r"工资|薪资|薪酬|发薪", unit):
                continue
            answer = f"工资发放日为每月 **{match.group(1)} 日**"
            nearby = "".join(units[position:position + 2])
            exception = re.search(r"(?:遇|如遇)[^。；]*(?:节假日|公休日)[^。；]*(?:提前|顺延)[^。；]*(?:工作日)[^。；]*", nearby)
            if exception:
                answer += f"；{exception.group(0).rstrip('。；')}"
            return f"{answer}。 [[{index}]]"
    return None


def _tenure_years(question: str) -> tuple[float, str] | None:
    if "半年" in question:
        return 0.5, "半年"
    month = re.search(r"(?:入职|工作)\s*(\d+(?:\.\d+)?)\s*个?月", question)
    if month:
        return float(month.group(1)) / 12, f"{month.group(1)}个月"
    year = re.search(r"(?:入职|工作|司龄|工龄)\s*(\d+(?:\.\d+)?)\s*年", question)
    if year:
        return float(year.group(1)), f"{year.group(1)}年"
    return None


def _eligibility_answer(question: str, evidence: list[dict[str, Any]]) -> str | None:
    tenure = _tenure_years(question)
    if not tenure or not re.search(r"年假|年休假", question):
        return None
    years, label = tenure
    for index, item in enumerate(evidence, start=1):
        for unit in _evidence_units(item):
            threshold = re.search(r"(?:连续工作|工作|入职)?\s*满\s*(\d+(?:\.\d+)?)\s*年[^。；]*(?:开始|方可|才可|享受)[^。；]*(?:年假|年休假)|(?:年假|年休假)[^。；]*(?:连续工作|工作|入职)?\s*满\s*(\d+(?:\.\d+)?)\s*年", unit)
            if not threshold:
                continue
            minimum = float(threshold.group(1) or threshold.group(2))
            if years < minimum:
                minimum_label = f"{minimum:g}年"
                return f"你目前入职 **{label}**，尚未达到“连续工作满 {minimum_label}”的年假享受条件，因此当前年假为 **0 天**。 [[{index}]]"
    return None


def _annual_leave_bracket_answer(question: str, evidence: list[dict[str, Any]]) -> str | None:
    tenure = _tenure_years(question)
    if not tenure or not re.search(r"年假|年休假", question):
        return None
    years, label = tenure
    for index, item in enumerate(evidence, start=1):
        continuous = re.sub(r"\s+", "", str(item.get("parent_content") or item.get("content") or ""))
        patterns = (
            r"满(?P<minimum>\d+(?:\.\d+)?)年(?:（含）)?不满(?P<maximum>\d+(?:\.\d+)?)年[^。；]{0,40}?(?P<days>\d+(?:\.\d+)?)天",
            r"(?P<minimum>\d+(?:\.\d+)?)年(?:（含）)?[—至到-](?P<maximum>\d+(?:\.\d+)?)年(?:（不含）)?[^。；]{0,40}?(?P<days>\d+(?:\.\d+)?)天",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, continuous):
                minimum = float(match.group("minimum"))
                maximum = float(match.group("maximum"))
                if minimum <= years < maximum:
                    days = float(match.group("days"))
                    return (
                        f"你目前司龄 **{label}**，属于“满 **{minimum:g}年**、不满 **{maximum:g}年**”档位，"
                        f"年假为 **{days:g}天**。 [[{index}]]"
                    )
    return None


def _absence_termination_answer(question: str, evidence: list[dict[str, Any]]) -> str | None:
    if not ("旷工" in question and re.search(r"开除|辞退|解除", question) and re.search(r"补偿|赔偿", question)):
        return None
    for index, item in enumerate(evidence, start=1):
        continuous = re.sub(r"\s+", "", str(item.get("parent_content") or item.get("content") or ""))
        complete_rule = re.search(
            r"连续旷工3个工作日[^。；]{0,50}(?:自然年度|一年|年度)内?累计旷工5个工作日"
            r"[^。；]{0,80}解除劳动合同[^。；]{0,40}不支付经济补偿",
            continuous,
        )
        if complete_rule:
            return (
                "连续旷工 **3个工作日**，或自然年度内累计旷工 **5个工作日**，"
                f"公司可以解除劳动合同，且**不支付经济补偿**。 [[{index}]]"
            )
    return None


def _repository_access_answer(question: str, evidence: list[dict[str, Any]]) -> str | None:
    if not re.search(r"核心代码仓库|rd-core", question, re.IGNORECASE):
        return None
    restricted_role = next((role for role in ("实习生", "外包人员", "外包", "试用期") if role in question), None)
    if not restricted_role:
        return None
    for index, item in enumerate(evidence, start=1):
        continuous = re.sub(r"\s+", "", str(item.get("parent_content") or item.get("content") or ""))
        if not (
            re.search(r"核心代码仓库（?GitLab分组rd-core）?仅研发序列正式员工可申请", continuous, re.IGNORECASE)
            and re.search(r"试用期未满1个月、实习生、外包人员不得开通生产环境及rd-core权限", continuous, re.IGNORECASE)
        ):
            continue
        return (
            "不能。核心代码仓库 **rd-core** 仅研发序列正式员工可申请；"
            f"试用期未满1个月、实习生和外包人员均不得开通生产环境及 rd-core 权限。 [[{index}]]"
        )
    return None


def _private_storage_answer(question: str, evidence: list[dict[str, Any]]) -> str | None:
    if not (re.search(r"私人.*(?:U盘|移动硬盘|OTG)|(?:U盘|移动硬盘|OTG).*私人", question, re.IGNORECASE) and re.search(r"拷|复制|外带|文件", question)):
        return None
    for index, item in enumerate(evidence, start=1):
        continuous = re.sub(r"\s+", "", str(item.get("parent_content") or item.get("content") or ""))
        if not (
            "禁止使用私人U盘、移动硬盘、手机OTG拷贝公司文件" in continuous
            and re.search(r"确需外带[^。；]{0,100}加密U盘（?XC-USB-xxx）?[^。；]{0,100}部门负责人审批", continuous, re.IGNORECASE)
        ):
            continue
        return (
            "禁止使用私人U盘、移动硬盘或手机 OTG 拷贝公司文件。确需外带时，须借用信息中心编号"
            f"加密U盘（**XC-USB-xxx**），填写移动介质借用单并经部门负责人审批。 [[{index}]]"
        )
    return None


def _difference_answer(question: str, evidence: list[dict[str, Any]]) -> str | None:
    match = re.search(r"(?P<left>[\u3400-\u9fffA-Za-z0-9-]{1,8})和(?P<right>[\u3400-\u9fffA-Za-z0-9-]{1,8}?)(?P<metric>通讯补贴|交通补贴|餐费补贴|住房补贴|补贴)(?:相差|差)多少", question)
    if not match:
        return None
    left, right, metric = match.group("left"), match.group("right"), match.group("metric")
    found: dict[str, tuple[float, int]] = {}
    for index, item in enumerate(evidence, start=1):
        rows = _evidence_table_rows(item)
        for header in rows:
            left_column = next((column for column, cell in enumerate(header) if left in cell), None)
            right_column = next((column for column, cell in enumerate(header) if right in cell), None)
            if left_column is None or right_column is None:
                continue
            for row in rows:
                if not any(metric in cell or (metric == "补贴" and "补贴" in cell) for cell in row):
                    continue
                if max(left_column, right_column) >= len(row):
                    continue
                left_amount = re.search(r"(\d+(?:\.\d+)?)", row[left_column])
                right_amount = re.search(r"(\d+(?:\.\d+)?)", row[right_column])
                if left_amount and right_amount:
                    found[left] = (float(left_amount.group(1)), index)
                    found[right] = (float(right_amount.group(1)), index)
                    break
        metric_column = next(
            (column for row in rows for column, cell in enumerate(row) if metric in cell or (metric == "补贴" and "补贴" in cell)),
            None,
        )
        if metric_column is not None:
            for row in rows:
                if metric_column >= len(row):
                    continue
                amount = re.search(r"(\d+(?:\.\d+)?)", row[metric_column])
                if not amount:
                    continue
                for role in (left, right):
                    if any(role in cell for cell in row) and role not in found:
                        found[role] = (float(amount.group(1)), index)
        for unit in _evidence_units(item):
            if metric not in unit:
                continue
            for role in (left, right):
                role_amount = re.search(rf"{re.escape(role)}[^。；]*?(\d+(?:\.\d+)?)\s*元", unit)
                if role_amount and role not in found:
                    found[role] = (float(role_amount.group(1)), index)
    if left not in found or right not in found:
        return None
    left_value, left_index = found[left]
    right_value, right_index = found[right]
    difference = abs(right_value - left_value)
    markers = f"[[{left_index}]]" if left_index == right_index else f"[[{left_index}]] [[{right_index}]]"
    return f"{left}的{metric}为 **{left_value:g} 元**，{right}为 **{right_value:g} 元**，两者相差 **{difference:g} 元**。 {markers}"


def _threshold_consequence_answer(question: str, evidence: list[dict[str, Any]]) -> str | None:
    amount_match = re.search(r"(\d+(?:\.\d+)?)\s*元", question)
    if not amount_match or not re.search(r"怎么处理|如何处理|如何处罚|怎么处罚|什么后果", question):
        return None
    amount = float(amount_match.group(1))
    subject = re.sub(r"\d+(?:\.\d+)?\s*元|怎么处理|如何处理|如何处罚|怎么处罚|什么后果|[？?，,。]", "", question).strip()
    subject_features = {
        run[position:position + 2]
        for run in re.findall(r"[\u3400-\u9fff]{2,}", subject)
        for position in range(max(1, len(run) - 1))
    }
    for index, item in enumerate(evidence, start=1):
        continuous = re.sub(r"\s+", "", str(item.get("parent_content") or item.get("content") or ""))
        for threshold_match in re.finditer(r"(?:金额)?超过(\d+(?:\.\d+)?)元", continuous):
            threshold = float(threshold_match.group(1))
            if amount <= threshold:
                continue
            window = continuous[max(0, threshold_match.start() - 140):threshold_match.end() + 140]
            if subject_features and not any(feature in window for feature in subject_features):
                continue
            consequences = [phrase for phrase in ("追回全额", "记大过", "解除劳动合同", "移送司法机关") if phrase in window]
            if consequences:
                return f"该金额为 **{amount:g} 元**，已超过制度规定的 **{threshold:g} 元**阈值，应当{'、'.join(consequences)}。 [[{index}]]"
        units = _evidence_units(item)
        for position, unit in enumerate(units):
            threshold_match = re.search(r"(?:金额)?\s*超过\s*(\d+(?:\.\d+)?)\s*元", unit)
            if not threshold_match or amount <= float(threshold_match.group(1)):
                continue
            neighborhood = units[max(0, position - 2):position + 1]
            joined = "".join(neighborhood)
            if subject_features and not any(feature in joined for feature in subject_features):
                continue
            consequences: list[str] = []
            for phrase in ("追回全额", "记大过", "解除劳动合同", "移送司法机关"):
                if phrase in joined and phrase not in consequences:
                    consequences.append(phrase)
            if consequences:
                threshold = float(threshold_match.group(1))
                return f"该金额为 **{amount:g} 元**，已超过制度规定的 **{threshold:g} 元**阈值，应当{'、'.join(consequences)}。 [[{index}]]"
    return None


def _expense_cap_answer(question: str, evidence: list[dict[str, Any]]) -> str | None:
    amount_match = re.search(r"(\d+(?:\.\d+)?)\s*元(?:\s*/\s*晚)?", question)
    if not amount_match or not re.search(r"能不能报|能否报|可以报|可否报|能报吗", question):
        return None
    requested = float(amount_match.group(1))
    indexed_units = [
        (index, unit)
        for index, item in enumerate(evidence, start=1)
        for unit in _evidence_units(item)
    ]
    roles = (
        "试用期员工", "部门负责人", "普通员工", "正式员工", "实习生", "外包人员",
        "董事长", "副总经理", "总经理", "总监", "经理", "主管", "员工",
    )
    role = next((item for item in roles if item in question), None)
    category: str | None = None
    category_index: int | None = None
    category_pattern = re.compile(r"(?:新一线|一线|二线及以下|二线|三线及以下|三线|其他)(?:城市)?")
    for index, unit in indexed_units:
        category_match = category_pattern.search(unit)
        if not category_match:
            continue
        cells = [cell.strip() for cell in re.split(r"[/、，,；;]", unit) if cell.strip()]
        place_tokens = [
            match.group(0)
            for cell in cells
            if (match := re.match(r"[\u3400-\u9fff]{2,8}", cell))
            and match.group(0) not in roles
            and not category_pattern.fullmatch(match.group(0))
        ]
        if any(token in question for token in place_tokens):
            category = category_match.group(0)
            category_index = index
            break
    if not category or not role:
        return None

    cap: float | None = None
    cap_index: int | None = None
    for index, unit in indexed_units:
        role_amount = re.search(
            rf"{re.escape(role)}[^。；，,|]{{0,28}}?(\d+(?:\.\d+)?)\s*元",
            unit,
        )
        if category in unit and role_amount:
            cap = float(role_amount.group(1))
            cap_index = index
            break
    if cap is None:
        for index, item in enumerate(evidence, start=1):
            rows = _evidence_table_rows(item)
            for header in rows:
                role_column = next((column for column, cell in enumerate(header) if role in cell), None)
                if role_column is None:
                    continue
                for row in rows:
                    if role_column >= len(row) or not any(city in question for city in re.findall(r"[\u3400-\u9fff]{2,}", "、".join(row))):
                        continue
                    amount = re.search(r"(\d+(?:\.\d+)?)", row[role_column])
                    row_category = next((cell for cell in row if re.fullmatch(r"新一线|一线|二线及以下|二线|三线及以下|三线|其他", cell)), None)
                    if amount and row_category:
                        cap = float(amount.group(1))
                        cap_index = index
                        category = row_category
                        category_index = index
                        break
                if cap is not None:
                    break
            if cap is not None:
                break
    if cap is None or cap_index is None:
        return None

    markers = {category_index, cap_index}
    exception_text = ""
    for index, unit in indexed_units:
        if re.search(r"指定酒店|协议酒店|会议酒店", unit) and re.search(r"批准|审批|同意", unit):
            exception_text = re.sub(r"^[^：:]{0,16}[：:]", "", unit).rstrip("。；")
            markers.add(index)
            break
    if not exception_text:
        for index, item in enumerate(evidence, start=1):
            continuous = re.sub(r"\s+", "", str(item.get("parent_content") or item.get("content") or ""))
            exception = re.search(r"(?:会议或客户|客户或会议)指定酒店超过标准的，?须在出差申请中注明并经分管副总(?:同意|批准)", continuous)
            if exception:
                exception_text = exception.group(0)
                markers.add(index)
                break
    marker_text = " ".join(f"[[{index}]]" for index in sorted(item for item in markers if item is not None))
    category_label = category if category.endswith("城市") else f"{category}城市"
    if requested <= cap:
        return f"可以。{role}在{category_label}的住宿上限为 **{cap:g} 元/晚**，本次 **{requested:g} 元/晚**未超标。 {marker_text}"
    excess = requested - cap
    answer = (
        f"不能按 **{requested:g} 元/晚**全额报销。{role}在{category_label}的住宿上限为 **{cap:g} 元/晚**，"
        f"超出的 **{excess:g} 元/晚**原则上需自行承担。"
    )
    if exception_text:
        answer += f"例外是：{exception_text}。"
    return f"{answer} {marker_text}"


def _late_duration_answer(question: str, evidence: list[dict[str, Any]]) -> str | None:
    if "迟到" not in question:
        return None
    minute_match = re.search(r"(\d+(?:\.\d+)?)\s*分钟", question)
    hour_match = re.search(r"(\d+(?:\.\d+)?)\s*小时", question)
    if not minute_match and not hour_match:
        return None
    minutes = float(minute_match.group(1)) if minute_match else float(hour_match.group(1)) * 60
    duration_label = f"{minutes:g}分钟"

    for index, item in enumerate(evidence, start=1):
        continuous = re.sub(r"\s+", "", str(item.get("parent_content") or item.get("content") or ""))
        if "迟到" not in continuous:
            continue
        answer: str | None = None
        if minutes <= 15:
            rule = re.search(r"迟到不足15分钟（?含）?的?，?当月第一次口头提醒，?不扣款；?当月第二次起每次扣款30元", continuous)
            if rule:
                answer = (
                    f"迟到 **{duration_label}** 属于“不足15分钟（含）”档位："
                    "当月第一次口头提醒、不扣款；当月第二次起每次扣款 **30元**。"
                )
        elif minutes < 30:
            rule = re.search(r"迟到超过15分钟不足30分钟的?，?每次扣款50元", continuous)
            if rule:
                answer = f"迟到 **{duration_label}** 属于“超过15分钟、不足30分钟”档位，每次扣款 **50元**。"
        elif minutes == 30:
            continue
        elif minutes < 120:
            rule = re.search(r"迟到超过30分钟不足2小时的?，?按旷工半天处理", continuous)
            if rule:
                answer = f"迟到 **{duration_label}** 属于“超过30分钟、不足2小时”档位，按 **旷工半天** 处理。"
        else:
            rule = re.search(r"迟到2小时及以上的?，?按旷工一天处理", continuous)
            if rule:
                answer = f"迟到 **{duration_label}** 达到“2小时及以上”档位，按 **旷工一天** 处理。"
        if not answer:
            continue
        cumulative = re.search(r"自然月内迟到累计达到3次的?，?无论单次时长，?加记旷工半天，?并取消当月全勤奖", continuous)
        if cumulative:
            answer += "另外，自然月内迟到累计达到 **3次**，还会加记旷工半天并取消当月全勤奖。"
        return f"{answer} [[{index}]]"
    return None


def fast_factual_answer(question: str, evidence: list[dict[str, Any]]) -> str | None:
    """Answer narrow, high-confidence structured lookups without an LLM."""

    # This resolver intentionally covers both clauses of the known policy rule,
    # so it is safe to run before the generic multi-part guard below.
    complete_multi_part = _absence_termination_answer(question, evidence)
    if complete_multi_part:
        return complete_multi_part

    # Every resolver below returns one narrow fact. Let the grounded answerer
    # synthesize multi-slot questions so a successful first resolver cannot
    # silently discard the remaining clauses.
    if _is_multi_part_question(question):
        return None

    for resolver in (
        _salary_date_answer,
        _eligibility_answer,
        _annual_leave_bracket_answer,
        _difference_answer,
        _threshold_consequence_answer,
        _expense_cap_answer,
        _late_duration_answer,
        _repository_access_answer,
        _private_storage_answer,
    ):
        answer = resolver(question, evidence)
        if answer:
            return answer

    if not re.search(r"上班时间|工作时间|办公时间|几点上班|几点下班", question):
        return None
    subject = re.split(r"上班时间|工作时间|办公时间|几点上班|几点下班", question, maxsplit=1)[0]
    subject = re.sub(r"^(?:请问|查询|查一下|我想知道)", "", subject).strip(" ，,。？?")
    for index, item in enumerate(evidence, start=1):
        compacted = compact_evidence_text(question, item, 420)
        for line in compacted.splitlines():
            if subject and subject not in line:
                continue
            times = re.findall(r"(?<!\d)([0-2]?\d:\d{2})(?!\d)", line)
            if len(times) < 2:
                continue
            label = subject or "该地点"
            answer = f"{label}的工作时间为 **{times[0]}–{times[1]}**"
            if len(times) >= 4:
                answer += f"，午休时间为 **{times[2]}–{times[3]}**"
            return f"{answer}。 [[{index}]]"
    return None


@dataclass
class GroundedAnswer:
    content: str
    citations: list[dict[str, Any]]
    suggestions: list[str]
    no_evidence: bool
    provider: str


class AnswerProvider(Protocol):
    async def build(self, question: str, search_results: list[dict[str, Any]]) -> GroundedAnswer: ...


def _excerpt(content: str, limit: int = 340) -> str:
    normalized = re.sub(r"\s+", " ", content).strip().lstrip("#- ")
    if len(normalized) <= limit:
        return normalized
    boundary = max(normalized.rfind("。", 0, limit), normalized.rfind("；", 0, limit), normalized.rfind(". ", 0, limit))
    return normalized[:boundary + 1 if boundary > limit // 2 else limit].rstrip() + "…"


class ExtractiveAnswerProvider:
    """Grounded local fallback used until an optional LLM provider is configured."""

    provider_id = "extractive-local-v1"

    def build(self, question: str, search_results: list[dict[str, Any]]) -> GroundedAnswer:
        evidence = select_evidence(question, search_results)
        if not evidence:
            return GroundedAnswer(
                content=(
                    "当前知识库中没有找到足够可靠的依据来回答这个问题。\n\n"
                    "你可以尝试补充更具体的关键词、切换知识库，或先在文档管理中上传相关资料。"
                    "为了避免误导，我不会在缺少依据时推测答案。"
                ),
                citations=[],
                suggestions=alternate_questions(question),
                no_evidence=True,
                provider=self.provider_id,
            )

        citations = []
        answer_lines = ["根据当前知识库检索到的原文，相关内容如下：", ""]
        headings: list[str] = []
        for index, item in enumerate(evidence, start=1):
            excerpt = _excerpt(compact_evidence_text(question, item, 340))
            answer_lines.append(f"- {excerpt} [[{index}]]")
            heading = str(item.get("heading") or "").strip()
            if heading and heading not in headings:
                headings.append(heading)
            citations.append(
                {
                    "id": index,
                    "chunk_id": item["chunk_id"],
                    "document_id": item["document_id"],
                    "document_name": item["document_name"],
                    "format": item["format"],
                    "page": item.get("page"),
                    "chapter": heading or "正文",
                    "updated_at": item["updated_at"],
                    "excerpt": excerpt,
                    "context": item.get("parent_content") or item["content"],
                    "parent_chunk_id": item.get("parent_chunk_id"),
                    "content_kind": item.get("content_kind", "text"),
                    "location": item.get("location"),
                    "score": evidence_score(item),
                }
            )
        answer_lines.extend(["", "以上内容按本地知识库原文整理，未加入资料之外的推测。"])
        suggestions = [f"继续查看“{heading}”" for heading in headings[:2]]
        suggestions.extend(["概括这些资料的关键要点", "比较不同来源的相关内容"])
        return GroundedAnswer(
            content="\n".join(answer_lines),
            citations=citations,
            suggestions=suggestions[:3],
            no_evidence=False,
            provider=self.provider_id,
        )
