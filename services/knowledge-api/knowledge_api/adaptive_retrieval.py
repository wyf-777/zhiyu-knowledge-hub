from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from .embeddings import EmbeddingManager, cosine_similarity
from .ingestion import DocumentProcessor
from .modeling import ModelManager, QueryContextPlan
from .retrieval_planning import build_retrieval_queries
from .retrieval_quality import RetrievalAssessment, assess_retrieval, validate_query_expansion


@dataclass(frozen=True)
class AdaptiveRetrievalOutcome:
    results: list[dict[str, Any]]
    queries: tuple[str, ...]
    initial_queries: tuple[str, ...]
    route: str
    initial_assessment: RetrievalAssessment
    final_assessment: RetrievalAssessment
    expansion_provider: str
    accepted_expansions: tuple[str, ...]
    rejected_expansions: tuple[dict[str, str], ...]
    expansion_latency_ms: int

    def diagnostics(self) -> dict[str, Any]:
        return {
            "retrieval_route": self.route,
            "retrieval_confidence_before": self.initial_assessment.confidence,
            "retrieval_confidence_after": self.final_assessment.confidence,
            "top_evidence_before": round(self.initial_assessment.top_evidence_score, 4),
            "top_evidence_after": round(self.final_assessment.top_evidence_score, 4),
            "query_expansion_provider": self.expansion_provider,
            "query_expansion_count": len(self.accepted_expansions),
            "query_expansion_rejected": [dict(item) for item in self.rejected_expansions],
            "query_expansion_latency_ms": self.expansion_latency_ms,
        }


def _planned_queries(roots: tuple[str, ...], *, limit: int = 5) -> list[str]:
    queries: list[str] = []
    for root_query in roots:
        for query in build_retrieval_queries(root_query):
            if query not in queries:
                queries.append(query)
            if len(queries) >= limit:
                return queries
    return queries


class AdaptiveRetriever:
    def __init__(
        self,
        processor: DocumentProcessor,
        embeddings: EmbeddingManager,
        model_manager: ModelManager,
    ) -> None:
        self.processor = processor
        self.embeddings = embeddings
        self.model_manager = model_manager

    async def retrieve(
        self,
        plan: QueryContextPlan,
        knowledge_base_ids: list[str],
        *,
        limit: int = 12,
        document_ids: list[str] | None = None,
    ) -> AdaptiveRetrievalOutcome:
        initial_queries = _planned_queries(plan.retrieval_questions)
        initial_results = await asyncio.to_thread(
            self.processor.search_many,
            initial_queries,
            knowledge_base_ids,
            limit,
            document_ids,
        )
        initial_assessment = assess_retrieval(initial_results)
        if initial_assessment.confidence != "recoverable":
            return AdaptiveRetrievalOutcome(
                initial_results,
                tuple(initial_queries),
                tuple(initial_queries),
                "single-pass",
                initial_assessment,
                initial_assessment,
                "not-required",
                (),
                (),
                0,
            )

        expansion_started_at = time.perf_counter()
        proposal = await self.model_manager.propose_query_expansion(plan.standalone_question, initial_results[:3])
        if proposal.queries:
            try:
                vectors = await asyncio.to_thread(
                    self.embeddings.embed,
                    [plan.standalone_question, *proposal.queries],
                )
                similarities = [cosine_similarity(vectors[0], vector) for vector in vectors[1:]]
            except (ValueError, TypeError, OSError):
                similarities = [0.0] * len(proposal.queries)
        else:
            similarities = []
        validated = validate_query_expansion(
            plan.standalone_question,
            proposal.queries,
            proposal.must_keep,
            similarities,
        )
        expansion_latency_ms = round((time.perf_counter() - expansion_started_at) * 1000)
        if not validated.accepted:
            return AdaptiveRetrievalOutcome(
                initial_results,
                tuple(initial_queries),
                tuple(initial_queries),
                "rewrite-unavailable" if not proposal.queries else "rewrite-rejected",
                initial_assessment,
                initial_assessment,
                proposal.provider,
                (),
                validated.rejected,
                expansion_latency_ms,
            )

        expanded_roots = tuple([plan.standalone_question, *validated.accepted])
        expanded_queries = _planned_queries(expanded_roots)
        expanded_results = await asyncio.to_thread(
            self.processor.search_many,
            expanded_queries,
            knowledge_base_ids,
            limit,
            document_ids,
        )
        expanded_assessment = assess_retrieval(expanded_results)
        use_expanded = expanded_assessment.top_evidence_score > initial_assessment.top_evidence_score
        return AdaptiveRetrievalOutcome(
            expanded_results if use_expanded else initial_results,
            tuple(expanded_queries if use_expanded else initial_queries),
            tuple(initial_queries),
            "adaptive-expanded" if use_expanded else "adaptive-no-gain",
            initial_assessment,
            expanded_assessment if use_expanded else initial_assessment,
            proposal.provider,
            validated.accepted,
            validated.rejected,
            expansion_latency_ms,
        )
