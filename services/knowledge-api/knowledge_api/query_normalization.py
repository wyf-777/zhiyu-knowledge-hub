from __future__ import annotations

import re

_TIME_QUESTION = re.compile(r"什么时候|何时|哪天|几号|什么时间")
_PAYMENT_ACTION = re.compile(r"发放|发薪|发工资|支付|到账|打款|领取|发(?:[？?]?$)")
_PAYMENT_DATE_TERM = re.compile(r"发薪日|发放日(?:期)?|支付日(?:期)?|到账日(?:期)?|打款日(?:期)?")


def normalize_query_semantics(value: str) -> str:
    """Add intent vocabulary while retaining every original condition."""

    normalized = re.sub(r"\s+", " ", value).strip()
    additions: list[str] = []
    if _TIME_QUESTION.search(normalized):
        additions.extend(("时间", "日期"))
        if _PAYMENT_ACTION.search(normalized):
            additions.extend(("发放", "支付", "到账", "发放日期", "发薪日"))
    if _PAYMENT_DATE_TERM.search(normalized):
        additions.extend(("时间", "日期", "发放", "支付", "到账", "发放日期", "发薪日"))
    unique_additions = [term for term in dict.fromkeys(additions) if term not in normalized]
    return " ".join([normalized, *unique_additions]).strip()
