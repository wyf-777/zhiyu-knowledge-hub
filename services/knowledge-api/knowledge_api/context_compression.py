from __future__ import annotations

import re
from typing import Any

from .embeddings import text_features
from .retrieval_planning import build_retrieval_queries
from .reranking import extract_anchors

_MARKDOWN_SEPARATOR = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")
_ENUMERATION_INTENT = re.compile(r"所有|全部|完整|逐一|罗列|列出|哪些|哪\s*\d+|几座|多少个")


def _plain_table_row(line: str) -> str:
    cells = [re.sub(r"\s+", " ", cell).strip() for cell in line.strip().strip("|").split("|")]
    return " / ".join(cell for cell in cells if cell)


def _units(text: str) -> list[tuple[int, str]]:
    units: list[tuple[int, str]] = []
    position = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _MARKDOWN_SEPARATOR.fullmatch(line) or line == "---":
            position += 1
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        if "|" in line:
            line = _plain_table_row(line)
        for sentence in re.split(r"(?<=[。！？；!?;])\s*", line):
            normalized = re.sub(r"\s+", " ", sentence).strip(" -*")
            if normalized:
                units.append((position, normalized))
                position += 1
    return units


def _table_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    previous: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if "|" not in line or _MARKDOWN_SEPARATOR.fullmatch(line):
            continue
        cells = [re.sub(r"\s+", " ", cell).strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or not any(cells):
            continue
        # Spreadsheet merged cells are commonly emitted as blank cells on the
        # following rows. Forward filling them lets one logical table group be
        # kept intact without relying on a particular workbook schema.
        if previous:
            cells = [cell or (previous[index] if index < len(previous) else "") for index, cell in enumerate(cells)]
        previous = cells
        rows.append(cells)
    return rows


def _complete_table_group(question: str, text: str, max_chars: int) -> str | None:
    if not _ENUMERATION_INTENT.search(question):
        return None
    rows = _table_rows(text)
    if len(rows) < 3:
        return None

    query_features = set(text_features(question))
    grouped_values: dict[tuple[int, str], int] = {}
    for row in rows[1:]:
        for column, cell in enumerate(row):
            if cell:
                grouped_values[(column, cell)] = grouped_values.get((column, cell), 0) + 1
    repeated_groups = [
        (len(set(text_features(value)) & query_features), count, column, value)
        for (column, value), count in grouped_values.items()
        if count >= 2 and set(text_features(value)) & query_features
    ]
    if repeated_groups:
        _, _, group_column, group_value = max(repeated_groups)
        selected = [row for row in rows[1:] if group_column < len(row) and row[group_column] == group_value]
        result = "\n".join(_plain_table_row("|" + "|".join(row) + "|") for row in selected)
        return result if len(result) <= max_chars else None

    best_index = max(
        range(1, len(rows)),
        key=lambda index: len(set(text_features(" ".join(rows[index]))) & query_features),
    )
    best_row = rows[best_index]
    group_column: int | None = None
    for index, cell in enumerate(best_row):
        cell_features = set(text_features(cell))
        if cell_features and cell_features & query_features:
            group_column = index
            break

    selected: list[list[str]]
    if group_column is not None and best_row[group_column]:
        group_value = best_row[group_column]
        selected = [row for row in rows[1:] if group_column < len(row) and row[group_column] == group_value]
    else:
        selected = rows[1:]
    if not selected:
        return None

    lines = [_plain_table_row("|" + "|".join(row) + "|") for row in selected]
    result = "\n".join(lines)
    if len(result) <= max_chars:
        return result
    # Never silently present a truncated list as complete. Returning None lets
    # the normal relevance compressor take over and the answer prompt disclose
    # that the supplied evidence is incomplete.
    return None


def _complete_rule_line(question: str, text: str, max_chars: int) -> str | None:
    match = re.search(r"(?:那)?(?P<topic>[\u3400-\u9fffA-Za-z0-9_-]{1,20}?)(?:问题)?(?:是)?(?:怎么|如何)规定", question)
    if not match:
        return None
    topic = match.group("topic").strip()
    candidates: list[tuple[int, int, str]] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or "|" in line or topic not in line or len(line) > max_chars:
            continue
        candidates.append((line.count(topic), len(re.findall(r"\d", line)), line))
    if not candidates:
        return None
    occurrences, digits, best = max(candidates)
    return best if occurrences >= 2 or digits >= 2 else None


def compact_evidence_text(question: str, item: dict[str, Any], max_chars: int = 480) -> str:
    """Select locally relevant lines from a parent block within a strict budget."""

    child = str(item.get("content") or "")
    parent = str(item.get("parent_content") or child)
    complete_group = _complete_table_group(question, parent, max_chars)
    if complete_group:
        return complete_group
    complete_rule = _complete_rule_line(question, parent, max_chars)
    if complete_rule:
        return complete_rule
    units = _units(parent if parent.strip() else child)
    if not units:
        return re.sub(r"\s+", " ", child).strip()[:max_chars]

    query_feature_sets = [set(text_features(query)) for query in build_retrieval_queries(question)]
    query_feature_sets = [features for features in query_feature_sets if features] or [set(text_features(question))]
    query_anchors = extract_anchors(question)
    child_features = set(text_features(child))
    scored: list[tuple[float, int, str]] = []
    for position, unit in units:
        features = set(text_features(unit))
        coverages = [len(features & query_features) / max(1, len(query_features)) for query_features in query_feature_sets]
        query_score = max(coverages, default=0.0)
        child_overlap = len(features & child_features) / max(1, len(features))
        anchor_bonus = 0.35 if query_anchors & extract_anchors(unit) else 0.0
        scored.append((query_score * 0.75 + child_overlap * 0.15 + anchor_bonus, position, unit))

    selected: dict[int, str] = {}
    for _, position, unit in sorted(scored, key=lambda value: (value[0], len(value[2])), reverse=True):
        if len(unit) > max_chars:
            unit = unit[:max_chars].rstrip() + "…"
        projected = sum(len(value) for value in selected.values()) + len(unit) + len(selected) * 2
        if projected > max_chars and selected:
            continue
        selected[position] = unit
        if len(selected) >= 4 or projected >= max_chars * 0.82:
            break

    if not selected:
        return re.sub(r"\s+", " ", child).strip()[:max_chars]
    return "\n".join(selected[position] for position in sorted(selected))[:max_chars]


def compact_evidence_context(question: str, evidence: list[dict[str, Any]], max_chars: int = 1800) -> str:
    if not evidence:
        return ""
    enumeration = bool(_ENUMERATION_INTENT.search(question))
    per_item = max(220, min(520, max_chars // len(evidence)))
    blocks: list[str] = []
    remaining = max_chars
    for index, item in enumerate(evidence, start=1):
        item_budget = min(per_item, remaining)
        if enumeration and index == 1:
            item_budget = min(1100, remaining)
        content = compact_evidence_text(question, item, item_budget)
        block = f"[证据{index}] {item['document_name']} · {item.get('heading') or '正文'}\n{content}".strip()
        if len(block) > remaining and blocks:
            break
        blocks.append(block[:remaining])
        remaining -= len(block) + 2
        if remaining <= 120:
            break
    return "\n\n".join(blocks)
