from __future__ import annotations

import re
from typing import Any

from .embeddings import text_features
from .retrieval_planning import build_retrieval_queries
from .reranking import extract_anchors

_MARKDOWN_SEPARATOR = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")


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


def compact_evidence_text(question: str, item: dict[str, Any], max_chars: int = 480) -> str:
    """Select locally relevant lines from a parent block within a strict budget."""

    child = str(item.get("content") or "")
    parent = str(item.get("parent_content") or child)
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
    per_item = max(220, min(520, max_chars // len(evidence)))
    blocks: list[str] = []
    remaining = max_chars
    for index, item in enumerate(evidence, start=1):
        content = compact_evidence_text(question, item, min(per_item, remaining))
        block = f"[证据{index}] {item['document_name']} · {item.get('heading') or '正文'}\n{content}".strip()
        if len(block) > remaining and blocks:
            break
        blocks.append(block[:remaining])
        remaining -= len(block) + 2
        if remaining <= 120:
            break
    return "\n\n".join(blocks)
