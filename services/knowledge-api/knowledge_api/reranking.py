from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

from .embeddings import text_features
from .query_normalization import normalize_query_semantics

RerankerProviderName = Literal["off", "feature"]


@dataclass(frozen=True)
class RerankerStatus:
    provider: str
    state: Literal["ready", "disabled", "error"]
    model_id: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class Reranker(Protocol):
    model_id: str

    def rerank(self, query: str, candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]: ...


def _chinese_number(value: str) -> int | None:
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    if not value or any(character not in digits and character not in units for character in value):
        return None
    if all(character in digits for character in value):
        return int("".join(str(digits[character]) for character in value))
    total = 0
    section = 0
    number = 0
    for character in value:
        if character in digits:
            number = digits[character]
            continue
        unit = units[character]
        if unit == 10000:
            section = (section + number) * unit
            total += section
            section = 0
            number = 0
        else:
            section += (number or 1) * unit
            number = 0
    return total + section + number


def extract_anchors(text: str) -> set[str]:
    lowered = text.lower()
    compact = re.sub(r"\s+", "", lowered)
    anchors = {
        item.lower()
        for item in re.findall(
            r"(?<![a-z0-9])([a-z]{1,12}[-_][a-z0-9_-]*\d[a-z0-9_-]*)(?![a-z0-9_-])",
            lowered,
            flags=re.IGNORECASE,
        )
    }
    units = r"(?:个?工作日|万元|元|天|年|月|页|%|次|家|份|小时|分钟)"
    for match in re.finditer(rf"(\d+(?:\.\d+)?)({units})", compact):
        value = float(match.group(1))
        unit = match.group(2)
        if unit == "万元":
            anchors.add(f"{int(value * 10000) if value.is_integer() else value * 10000:g}元")
        else:
            anchors.add(f"{match.group(1)}{unit}")
    chinese_units = r"(?:个?工作日|万元|元|天|年|月|页|次|家|份|小时|分钟)"
    for match in re.finditer(rf"([零一二两三四五六七八九十百千]+)({chinese_units})", compact):
        converted = _chinese_number(match.group(1))
        if converted is not None:
            unit = match.group(2)
            anchors.add(f"{converted * 10000}元" if unit == "万元" else f"{converted}{unit}")
    return anchors


def required_query_anchors(query: str) -> set[str]:
    """Return anchors that a candidate must literally support.

    Identifiers remain strict. A numeric value used to locate a range (for
    example an employee with six years of service) is an input condition, not
    an expected answer literal, so requiring the source to contain that exact
    value incorrectly suppresses the matching bracket.
    """

    anchors = extract_anchors(query)
    identifiers = {anchor for anchor in anchors if re.search(r"[a-z]", anchor, re.IGNORECASE)}
    range_or_input_condition = re.search(
        r"司龄|工龄|年龄|入职\s*(?:半|\d)|工作\s*(?:半|\d)|满\s*\d|不满\s*\d|不足\s*\d|"
        r"超过\s*\d|以内|以上|以下|档位|区间|怎么处理|如何处理|如何处罚|什么后果|"
        r"能不能报|能否报|可以报|可否报|能不能住|差多少|相差多少|迟到|怎么算|如何计算",
        query,
    )
    if range_or_input_condition:
        return identifiers
    return anchors


_SEMANTIC_ALIASES = (
    ("回到", "结束"),
    ("回来", "结束"),
    ("几天内", "工作日内"),
    ("几天", "工作日"),
    ("有没有", "是否"),
    ("虚假报销", "虚假发票 重复报销 违规报销"),
    ("差多少", "差额 标准"),
)


def _feature_tokens(value: str) -> set[str]:
    normalized = normalize_query_semantics(value)
    for source, target in _SEMANTIC_ALIASES:
        normalized = normalized.replace(source, target)
    return set(text_features(normalized))


def _coverage(query_features: set[str], value: str) -> float:
    if not query_features:
        return 0.0
    return len(query_features & _feature_tokens(value)) / len(query_features)


class DisabledReranker:
    model_id = "disabled"

    def rerank(self, query: str, candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        del query
        ranked: list[dict[str, Any]] = []
        for candidate in candidates:
            item = dict(candidate)
            item["retrieval_score"] = round(float(item.get("score", 0.0)), 4)
            item["rerank_score"] = item["retrieval_score"]
            item["evidence_score"] = item["retrieval_score"]
            item["reranker_model_id"] = self.model_id
            ranked.append(item)
        return sorted(ranked, key=lambda item: float(item["score"]), reverse=True)[:limit]


class LocalFeatureReranker:
    """Deterministic local reranker for exact terms, headings and numeric anchors.

    This is deliberately not described as a Cross-Encoder. It provides a small,
    dependency-free reranking baseline and a stable adapter boundary for a future
    model-backed implementation.
    """

    model_id = "local-feature-reranker-v1"

    def rerank(self, query: str, candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        query_features = _feature_tokens(query)
        query_anchors = required_query_anchors(query)
        ranked: list[dict[str, Any]] = []
        for candidate in candidates:
            item = dict(candidate)
            content = str(item.get("content", ""))
            heading = str(item.get("heading", ""))
            document_name = str(item.get("document_name", ""))
            content_coverage = _coverage(query_features, content)
            heading_coverage = _coverage(query_features, heading)
            title_coverage = _coverage(query_features, document_name)
            candidate_anchors = extract_anchors(f"{heading}\n{content}")
            anchor_coverage = len(query_anchors & candidate_anchors) / len(query_anchors) if query_anchors else content_coverage
            feature_score = (
                content_coverage * 0.50
                + heading_coverage * 0.14
                + title_coverage * 0.06
                + anchor_coverage * 0.30
            )
            if query_anchors and not query_anchors & candidate_anchors:
                feature_score *= 0.30
            retrieval_score = max(0.0, min(1.0, float(item.get("score", 0.0))))
            rerank_score = retrieval_score * 0.50 + feature_score * 0.50
            evidence_score = min(1.0, retrieval_score * 0.50 + feature_score * 0.80)
            item["retrieval_score"] = round(retrieval_score, 4)
            item["rerank_score"] = round(rerank_score, 4)
            item["rerank_feature_score"] = round(feature_score, 4)
            item["evidence_score"] = round(evidence_score, 4)
            item["reranker_model_id"] = self.model_id
            ranked.append(item)
        return sorted(ranked, key=lambda item: float(item["rerank_score"]), reverse=True)[:limit]


class RerankerManager:
    def __init__(self, provider: RerankerProviderName = "feature") -> None:
        if provider == "feature":
            self.provider: Reranker = LocalFeatureReranker()
        elif provider == "off":
            self.provider = DisabledReranker()
        else:
            raise ValueError("不支持的 Reranker 提供器。")
        self.requested_provider = provider

    @property
    def model_id(self) -> str:
        return self.provider.model_id

    def rerank(self, query: str, candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        return self.provider.rerank(query, candidates, limit)

    def status(self) -> RerankerStatus:
        if self.requested_provider == "off":
            return RerankerStatus("off", "disabled", self.model_id, "独立重排已关闭，仅使用混合召回排序。")
        return RerankerStatus("feature", "ready", self.model_id, "正在使用不依赖模型下载的本地特征重排器。")
