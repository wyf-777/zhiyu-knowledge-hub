from __future__ import annotations

import re

MAX_RETRIEVAL_QUERIES = 5

_QUESTION_WORDS = re.compile(
    r"找谁|谁|怎么|如何|多少|几(?:天|个|年|次)?|能不能|能否|可不可以|可否|可以|能|是否|有没有|有无|什么|哪(?:些|个)?|为何|为什么"
)
_SUBJECT_STOP_WORDS = re.compile(r"(?:请问|麻烦|帮我|帮忙|查询|查找|一下|相关|规定|制度)+")
_NUMERIC_RANGE_VALUE = re.compile(r"\d+(?:\.\d+)?\s*(?:个?工作日|年|月|天|岁)")

_QUERY_ALIASES = (
    ("拷文件", "复制文件 外带文件"),
    ("U盘", "U盘 移动存储 加密U盘"),
    ("u盘", "U盘 移动存储 加密U盘"),
    ("开除", "解除劳动合同"),
    ("补偿", "经济补偿"),
    ("找谁开通", "开通联系人 负责部门 联系方式"),
    ("电脑配置", "电脑配置标准 显示器"),
)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n，,。；;？?")


def _subject_prefix(first_clause: str) -> str:
    match = _QUESTION_WORDS.search(first_clause)
    prefix = first_clause[:match.start()] if match else first_clause
    prefix = _SUBJECT_STOP_WORDS.sub("", prefix)
    prefix = re.sub(r"(?:的|是|要|想)$", "", prefix)
    return _normalize(prefix)


def _append_unique(queries: list[str], value: str) -> None:
    normalized = _normalize(value)
    compact = re.sub(r"\s+", "", normalized).lower()
    if len(compact) < 2:
        return
    if any(re.sub(r"\s+", "", item).lower() == compact for item in queries):
        return
    queries.append(normalized)


def build_retrieval_queries(question: str) -> list[str]:
    """Create a small deterministic query plan without an extra LLM round trip.

    The first item is always the original/re-written question. Additional items
    isolate independent answer slots and add generic policy terminology. No
    corpus text or network service is used here.
    """

    original = _normalize(question)
    if not original:
        return []
    queries = [original]

    clauses = [_normalize(item) for item in re.split(r"[？?；;]+", original) if _normalize(item)]
    if len(clauses) > 1:
        subject = _subject_prefix(clauses[0])
        for index, clause in enumerate(clauses):
            if index == 0 or not subject or subject.lower() in clause.lower():
                _append_unique(queries, clause)
            else:
                _append_unique(queries, f"{subject} {clause}")

    if re.search(r"(?:能|可以|可不可以|能否|可否|是否|有没有|有无).*(?:吗|？|\?)?$", original):
        subject = _subject_prefix(clauses[0] if clauses else original)
        if subject:
            _append_unique(queries, f"{subject} 是否允许 规定 条件 例外 禁止")

    if re.search(r"司龄|工龄|年龄|满\s*\d|不满\s*\d|不足\s*\d|超过\s*\d|以内|以上|以下|档位|区间", original):
        range_query = _NUMERIC_RANGE_VALUE.sub(" ", original)
        range_query = re.sub(r"\d+(?:\.\d+)?", " ", range_query)
        _append_unique(queries, f"{range_query} 标准 档位 条件")

    alias_query = original
    for source, target in _QUERY_ALIASES:
        alias_query = alias_query.replace(source, target)
    if alias_query != original:
        _append_unique(queries, alias_query)

    return queries[:MAX_RETRIEVAL_QUERIES]


def alternate_questions(question: str, limit: int = 2) -> list[str]:
    """Return clickable questions, never UI instruction templates."""

    planned = build_retrieval_queries(question)[1:]
    suggestions = [f"{item.rstrip('？?')}？" for item in planned]
    if suggestions:
        return suggestions[:limit]
    subject = _subject_prefix(question) or _normalize(question)
    return [
        f"当前知识库中关于“{subject}”有哪些明确规定？",
        f"请列出“{subject}”相关的原文依据。",
    ][:limit]
