from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .answering import MIN_EVIDENCE_SCORE, evidence_score
from .reranking import extract_anchors

RetrievalConfidence = Literal["sufficient", "recoverable", "unsupported"]


@dataclass(frozen=True)
class RetrievalAssessment:
    confidence: RetrievalConfidence
    top_evidence_score: float
    top_vector_score: float
    candidate_count: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ValidatedQueryExpansion:
    accepted: tuple[str, ...]
    rejected: tuple[dict[str, str], ...]
    must_keep: tuple[str, ...]

    @property
    def drift_detected(self) -> bool:
        return bool(self.rejected)


def assess_retrieval(results: list[dict[str, Any]]) -> RetrievalAssessment:
    """Classify the first pass without weakening the grounded-answer threshold."""

    if not results:
        return RetrievalAssessment("unsupported", 0.0, 0.0, 0, "no-candidates")
    top_evidence = max(evidence_score(item) for item in results)
    top_vector = max(float(item.get("vector_score", 0.0)) for item in results)
    if top_evidence >= MIN_EVIDENCE_SCORE:
        return RetrievalAssessment("sufficient", top_evidence, top_vector, len(results), "grounded-evidence")
    # A semantically related candidate below the answer threshold is a recall
    # problem worth one constrained rewrite. Truly unrelated queries still
    # refuse immediately and do not pay for a model round trip.
    if top_evidence >= 0.22 or top_vector >= 0.52:
        return RetrievalAssessment("recoverable", top_evidence, top_vector, len(results), "related-below-threshold")
    return RetrievalAssessment("unsupported", top_evidence, top_vector, len(results), "weak-or-unrelated")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).strip("，,。；;？?").lower()


def _polarity_is_preserved(original: str, candidate: str) -> bool:
    negative = re.compile(r"禁止|不得|不能|不可以|未|没有|无需|不需要")
    permission_question = re.compile(r"能不能|可不可以|能否|可否|是否允许")
    if negative.search(original) and not negative.search(candidate):
        return False
    if permission_question.search(original) and not (permission_question.search(candidate) or negative.search(candidate)):
        return False
    return True


def validate_query_expansion(
    original: str,
    proposed_queries: list[str] | tuple[str, ...],
    must_keep: list[str] | tuple[str, ...],
    similarities: list[float],
    *,
    minimum_similarity: float = 0.52,
    limit: int = 3,
) -> ValidatedQueryExpansion:
    """Accept only local rewrites that remain equivalent to the original.

    The original query is not handled here because callers must always retain
    it as the first retrieval route. Rejected rewrites are diagnostic only and
    can never replace user wording.
    """

    normalized_original = _normalize(original)
    original_anchors = extract_anchors(original)
    safe_constraints = tuple(
        dict.fromkeys(
            str(term).strip()
            for term in must_keep
            if 1 <= len(str(term).strip()) <= 24 and _normalize(str(term)) in normalized_original
        )
    )
    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    for candidate, similarity in zip(proposed_queries, similarities, strict=False):
        query = re.sub(r"\s+", " ", str(candidate)).strip().strip('"“”')
        reason = ""
        if not 2 <= len(query) <= 160:
            reason = "invalid-length"
        elif _normalize(query) == normalized_original:
            reason = "duplicate-original"
        elif any(_normalize(query) == _normalize(item) for item in accepted):
            reason = "duplicate-expansion"
        elif float(similarity) < minimum_similarity:
            reason = "semantic-drift"
        elif original_anchors - extract_anchors(query):
            reason = "anchor-loss"
        elif safe_constraints and any(_normalize(term) not in _normalize(query) for term in safe_constraints):
            reason = "constraint-loss"
        elif not _polarity_is_preserved(original, query):
            reason = "polarity-loss"
        if reason:
            rejected.append({"query": query[:160], "reason": reason})
            continue
        accepted.append(query)
        if len(accepted) >= limit:
            break
    return ValidatedQueryExpansion(tuple(accepted), tuple(rejected), safe_constraints)
