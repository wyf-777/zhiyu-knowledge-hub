from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .document_parsing import OcrUnavailableError, RapidOcrEngine, chunk_sections, extract_sections
from .embeddings import EmbeddingManager, hashing_embed as embed_text, text_features
from .reranking import RerankerManager, extract_anchors, required_query_anchors
from .storage import KnowledgeStore

RRF_K = 60


def _fts_query(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9_.-]{2,}", normalized):
        terms.append(token)
    for run in re.findall(r"[\u3400-\u9fff]+", normalized):
        if len(run) >= 3:
            terms.extend(run[index:index + 3] for index in range(len(run) - 2))
    unique_terms = list(dict.fromkeys(terms))[:16]
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in unique_terms)


class DocumentProcessor:
    def __init__(self, store: KnowledgeStore, embeddings: EmbeddingManager | None = None, reranker: RerankerManager | None = None) -> None:
        self.store = store
        self.embeddings = embeddings or EmbeddingManager("hashing")
        self.reranker = reranker or RerankerManager("feature")
        self.ocr = RapidOcrEngine()

    def process(self, document_id: str, job_id: str) -> None:
        document = self.store.get_document(document_id)
        if not document:
            return
        path = (self.store.data_dir / document["object_path"]).resolve()
        try:
            if not path.is_relative_to(self.store.documents_dir):
                raise ValueError("文档路径不在受控存储目录中")
            self.store.update_document(document_id, status="processing")
            self.store.update_job(job_id, stage="parse", status="running", progress=18)
            sections = extract_sections(
                path,
                str(document["source_type"]),
                ocr_engine=self.ocr,
                on_ocr=lambda _: self.store.update_job(job_id, stage="ocr", status="running", progress=34),
            )
            if not any(section.text.strip() for section in sections):
                raise ValueError("文档没有可提取或可识别的文本")
            self.store.update_job(job_id, stage="chunk", status="running", progress=48)
            chunks = chunk_sections(sections)
            if not chunks:
                raise ValueError("文档切分后没有可索引内容")
            self.store.update_job(job_id, stage="embedding", status="running", progress=72)
            vectors = self.embeddings.embed([str(chunk["content"]) for chunk in chunks])
            if len(vectors) != len(chunks):
                raise ValueError("Embedding 返回数量与文本块数量不一致")
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk["vector"] = vector
            self.store.update_job(job_id, stage="index", status="running", progress=92)
            self.store.replace_chunks(
                document_id,
                chunks,
                embedding_model_id=self.embeddings.model_id,
                embedding_dimensions=self.embeddings.dimensions,
                index_version=self.embeddings.index_version,
            )
            self.store.update_document(document_id, status="ready", chunk_count=len(chunks))
            self.store.update_job(job_id, stage="completed", status="succeeded", progress=100)
        except Exception as error:  # noqa: BLE001
            message = str(error)[:500] or "文档处理失败"
            error_code = "OCR_UNAVAILABLE" if isinstance(error, OcrUnavailableError) else "DOCUMENT_PROCESSING_FAILED"
            self.store.update_document(document_id, status="failed", error_code=error_code, error_message=message)
            self.store.update_job(job_id, stage="failed", status="failed", progress=100, error_code=error_code, error_message=message)

    def _search_single(
        self,
        query: str,
        query_vector: list[float],
        chunks: list[dict[str, Any]],
        knowledge_base_ids: list[str],
        limit: int,
        document_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        query_features = set(text_features(query))
        query_anchors = required_query_anchors(query)
        scored: list[dict[str, Any]] = []
        for chunk in chunks:
            chunk_vector = json.loads(chunk["vector"])
            compatible_vector = (
                chunk.get("embedding_model_id") == self.embeddings.model_id
                and int(chunk.get("embedding_dimensions") or 0) == self.embeddings.dimensions
                and len(chunk_vector) == len(query_vector)
            )
            vector_score = max(0.0, sum(a * b for a, b in zip(query_vector, chunk_vector, strict=True))) if compatible_vector else 0.0
            chunk_features = set(text_features(chunk["content"]))
            lexical_score = len(query_features & chunk_features) / max(1, len(query_features))
            semantic_score = vector_score * 0.82 + lexical_score * 0.18
            scored.append({
                "chunk_id": chunk["id"], "document_id": chunk["document_id"], "knowledge_base_id": chunk["knowledge_base_id"],
                "document_name": chunk["source_name"], "format": str(chunk["source_type"]).upper(), "page": chunk["page_start"],
                "heading": chunk["heading"], "content": chunk["content"], "content_kind": chunk.get("content_kind", "text"),
                "parent_chunk_id": chunk.get("parent_chunk_id") or chunk["id"],
                "parent_content": chunk.get("parent_content") or chunk["content"],
                "location": json.loads(chunk["location_json"]) if chunk.get("location_json") else None,
                "semantic_score": semantic_score,
                "vector_score": round(vector_score, 4), "lexical_score": round(lexical_score, 4),
                "embedding_model_id": chunk.get("embedding_model_id"), "updated_at": chunk["document_updated_at"],
            })

        semantic_ranked = sorted(scored, key=lambda item: item["semantic_score"], reverse=True)
        semantic_ranks = {item["chunk_id"]: rank for rank, item in enumerate(semantic_ranked, start=1) if item["semantic_score"] > 0}
        fts_results = self.store.search_fts(_fts_query(query), knowledge_base_ids, document_ids, max(limit * 5, 40))
        keyword_ranks = {item["chunk_id"]: rank for rank, item in enumerate(fts_results, start=1)}
        maximum_rrf = 2 / (RRF_K + 1)
        results: list[dict[str, Any]] = []
        for item in scored:
            semantic_rank = semantic_ranks.get(item["chunk_id"])
            keyword_rank = keyword_ranks.get(item["chunk_id"])
            if semantic_rank is None and keyword_rank is None:
                continue
            rrf_raw = (1 / (RRF_K + semantic_rank) if semantic_rank else 0.0) + (1 / (RRF_K + keyword_rank) if keyword_rank else 0.0)
            rrf_score = rrf_raw / maximum_rrf
            item["keyword_score"] = round(1 / keyword_rank, 4) if keyword_rank else 0.0
            base_score = rrf_score * 0.72 + item.pop("semantic_score") * 0.28
            support_factor = 1.0
            candidate_anchors = extract_anchors(f'{item["heading"]}\n{item["content"]}')
            if query_anchors:
                anchor_coverage = len(query_anchors & candidate_anchors) / len(query_anchors)
                support_factor = 1.0 if anchor_coverage == 1.0 else min(0.12, float(item["lexical_score"]) / 0.22)
            elif self.embeddings.model_id == "hashing-vector-v1":
                support_factor = min(1.0, float(item["lexical_score"]) / 0.22)
            item["support_factor"] = round(support_factor, 4)
            item["score"] = round(base_score * support_factor, 4)
            item["retrieval_mode"] = "hybrid" if semantic_rank and keyword_rank else "semantic" if semantic_rank else "keyword"
            results.append(item)
        ranked = self.reranker.rerank(query, results, max(limit * 3, limit))
        deduplicated: list[dict[str, Any]] = []
        seen_parents: set[str] = set()
        for item in ranked:
            parent_id = str(item.get("parent_chunk_id") or item["chunk_id"])
            if parent_id in seen_parents:
                continue
            seen_parents.add(parent_id)
            deduplicated.append(item)
            if len(deduplicated) >= limit:
                break
        return deduplicated

    def search(self, query: str, knowledge_base_ids: list[str], limit: int, document_ids: list[str] | None = None) -> list[dict[str, Any]]:
        return self.search_many([query], knowledge_base_ids, limit, document_ids)

    def search_many(
        self,
        queries: list[str],
        knowledge_base_ids: list[str],
        limit: int,
        document_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_queries = list(dict.fromkeys(query.strip() for query in queries if query.strip()))[:5]
        if not normalized_queries:
            return []
        chunks = self.store.list_search_chunks(knowledge_base_ids, document_ids)
        if not chunks:
            return []
        query_vectors = self.embeddings.embed(normalized_queries)
        per_query = [
            self._search_single(query, vector, chunks, knowledge_base_ids, max(limit * 2, limit), document_ids)
            for query, vector in zip(normalized_queries, query_vectors, strict=True)
        ]
        if len(per_query) == 1:
            return per_query[0][:limit]

        fused: dict[str, dict[str, Any]] = {}
        for query_index, results in enumerate(per_query):
            for rank, result in enumerate(results, start=1):
                parent_id = str(result.get("parent_chunk_id") or result["chunk_id"])
                if parent_id not in fused:
                    fused[parent_id] = {
                        **result,
                        "query_hits": set(),
                        "query_fusion_raw": 0.0,
                        "best_query_rank": rank,
                    }
                item = fused[parent_id]
                item["query_hits"].add(query_index)
                item["query_fusion_raw"] += 1 / (RRF_K + rank)
                item["best_query_rank"] = min(int(item["best_query_rank"]), rank)
                if float(result.get("evidence_score", 0.0)) > float(item.get("evidence_score", 0.0)):
                    preserved = {"query_hits": item["query_hits"], "query_fusion_raw": item["query_fusion_raw"], "best_query_rank": item["best_query_rank"]}
                    item.update(result)
                    item.update(preserved)

        maximum_fusion = len(normalized_queries) / (RRF_K + 1)
        ranked: list[dict[str, Any]] = []
        for item in fused.values():
            fusion_score = float(item.pop("query_fusion_raw")) / maximum_fusion
            coverage = len(item["query_hits"]) / len(normalized_queries)
            best_rerank = float(item.get("rerank_score", item.get("score", 0.0)))
            item["query_fusion_score"] = round(fusion_score, 4)
            item["query_coverage"] = round(coverage, 4)
            item["score"] = round(best_rerank * 0.55 + fusion_score * 0.35 + coverage * 0.10, 4)
            ranked.append(item)
        ranked.sort(key=lambda item: (float(item["score"]), float(item.get("evidence_score", 0.0))), reverse=True)

        # Preserve at least one strong result for every answer slot before
        # filling the remaining positions with globally fused candidates.
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for results in per_query:
            strong = next((item for item in results if float(item.get("evidence_score", 0.0)) >= 0.45), None)
            if strong is None:
                continue
            parent_id = str(strong.get("parent_chunk_id") or strong["chunk_id"])
            if parent_id not in seen:
                selected.append(fused[parent_id])
                seen.add(parent_id)
        for item in ranked:
            parent_id = str(item.get("parent_chunk_id") or item["chunk_id"])
            if parent_id in seen:
                continue
            selected.append(item)
            seen.add(parent_id)
            if len(selected) >= limit:
                break
        for item in selected:
            item["query_hits"] = sorted(item["query_hits"])
        return selected[:limit]
