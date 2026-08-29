from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

from .answering import MIN_EVIDENCE_SCORE, evidence_score
from .embeddings import EmbeddingManager, EmbeddingProviderName
from .ingestion import DocumentProcessor
from .reranking import RerankerManager, RerankerProviderName
from .storage import KnowledgeStore

REAL_CORPUS_TYPE = "sanitized-enterprise"
SUPPORTED_CORPUS_TYPES = {"synthetic", REAL_CORPUS_TYPE}


def validate_corpus_provenance(dataset: dict[str, Any], *, require_real_corpus: bool = False) -> dict[str, str]:
    provenance = dataset.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("评测集缺少 provenance，无法区分合成数据与脱敏企业语料")
    corpus_type = str(provenance.get("type") or "")
    if corpus_type not in SUPPORTED_CORPUS_TYPES:
        raise ValueError("provenance.type 必须是 synthetic 或 sanitized-enterprise")
    if require_real_corpus and corpus_type != REAL_CORPUS_TYPE:
        raise ValueError("当前评测集不是经脱敏企业语料（sanitized-enterprise），已拒绝作为真实语料评测")
    organization = str(provenance.get("organization") or "anonymous")
    reviewed_at = str(provenance.get("reviewed_at") or "")
    if corpus_type == REAL_CORPUS_TYPE and not reviewed_at:
        raise ValueError("脱敏企业语料必须填写 provenance.reviewed_at")
    return {"type": corpus_type, "organization": organization, "reviewed_at": reviewed_at}


def recall_at_k(returned: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0 if not returned[:k] else 0.0
    return len(set(returned[:k]) & relevant) / len(relevant)


def reciprocal_rank(returned: list[str], relevant: set[str]) -> float:
    for rank, item in enumerate(returned, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(returned: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0 if not returned[:k] else 0.0
    gains = [1.0 if item in relevant else 0.0 for item in returned[:k]]
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal_count = min(len(relevant), k)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / ideal if ideal else 0.0


def run_retrieval_evaluation(
    dataset: dict[str, Any],
    data_dir: Path,
    *,
    top_k: int = 5,
    embedding_provider: str = "hashing",
    embedding_base_url: str = "http://127.0.0.1:11434",
    embedding_model: str = "bge-m3",
    reranker_provider: str = "feature",
) -> dict[str, Any]:
    provenance = validate_corpus_provenance(dataset)
    store = KnowledgeStore(data_dir)
    embeddings = EmbeddingManager(cast(EmbeddingProviderName, embedding_provider), base_url=embedding_base_url, model=embedding_model)
    reranker = RerankerManager(cast(RerankerProviderName, reranker_provider))
    processor = DocumentProcessor(store, embeddings, reranker)
    knowledge_base_id = str(dataset.get("knowledge_base_id") or "enterprise")

    for source in dataset.get("documents", []):
        source_name = str(source["name"])
        content = str(source["content"])
        suffix = Path(source_name).suffix.lower() or ".txt"
        source_type = suffix.lstrip(".") if suffix in {".txt", ".md"} else "txt"
        storage_id = hashlib.sha256(source_name.encode("utf-8")).hexdigest()[:20]
        relative_path = Path("documents") / knowledge_base_id / f"eval-{storage_id}{suffix}"
        absolute_path = store.data_dir / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_text(content, encoding="utf-8")
        document = store.create_document(
            knowledge_base_id=knowledge_base_id,
            source_name=source_name,
            source_type=source_type,
            object_path=relative_path.as_posix(),
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            size_bytes=len(content.encode("utf-8")),
            mime_type="text/markdown" if source_type == "md" else "text/plain",
        )
        job = store.create_job(str(document["id"]))
        processor.process(str(document["id"]), str(job["id"]))

    case_reports: list[dict[str, Any]] = []
    for case in dataset.get("cases", []):
        results = processor.search(str(case["query"]), [knowledge_base_id], max(top_k, 8))
        returned = list(dict.fromkeys(str(item["document_name"]) for item in results))
        relevant = {str(item) for item in case.get("relevant_documents", [])}
        returned_chunks = list(dict.fromkeys(f'{item["document_name"]}#{item.get("heading", "")}' for item in results))
        relevant_chunks = {
            f'{item["document"]}#{item.get("heading", "")}'
            for item in case.get("relevant_chunks", [])
        }
        answerable = bool(case.get("answerable", bool(relevant)))
        predicted_answerable = bool(results and evidence_score(results[0]) >= MIN_EVIDENCE_SCORE)
        case_reports.append({
            "id": str(case["id"]),
            "query": str(case["query"]),
            "returned": returned[:top_k],
            "top_results": [
                {
                    "document": str(item["document_name"]),
                    "heading": str(item.get("heading", "")),
                    "score": float(item.get("score", 0.0)),
                    "retrieval_score": float(item.get("retrieval_score", 0.0)),
                    "rerank_score": float(item.get("rerank_score", 0.0)),
                    "evidence_score": evidence_score(item),
                }
                for item in results[:top_k]
            ],
            "recall_at_k": round(recall_at_k(returned, relevant, top_k), 4) if relevant else None,
            "mrr": round(reciprocal_rank(returned, relevant), 4) if relevant else None,
            "ndcg_at_k": round(ndcg_at_k(returned, relevant, top_k), 4) if relevant else None,
            "chunk_recall_at_k": round(recall_at_k(returned_chunks, relevant_chunks, top_k), 4) if relevant_chunks else None,
            "chunk_mrr": round(reciprocal_rank(returned_chunks, relevant_chunks), 4) if relevant_chunks else None,
            "chunk_ndcg_at_k": round(ndcg_at_k(returned_chunks, relevant_chunks, top_k), 4) if relevant_chunks else None,
            "answerability_correct": answerable == predicted_answerable,
            "top_score": float(results[0]["score"]) if results else 0.0,
            "top_vector_score": float(results[0].get("vector_score", 0.0)) if results else 0.0,
            "top_lexical_score": float(results[0].get("lexical_score", 0.0)) if results else 0.0,
            "top_keyword_score": float(results[0].get("keyword_score", 0.0)) if results else 0.0,
            "top_retrieval_score": float(results[0].get("retrieval_score", 0.0)) if results else 0.0,
            "top_rerank_score": float(results[0].get("rerank_score", 0.0)) if results else 0.0,
            "top_evidence_score": evidence_score(results[0]) if results else 0.0,
        })

    ranked_reports = [item for item in case_reports if item["recall_at_k"] is not None]
    ranked_count = max(1, len(ranked_reports))
    chunk_reports = [item for item in case_reports if item["chunk_recall_at_k"] is not None]
    chunk_count = max(1, len(chunk_reports))
    count = max(1, len(case_reports))
    return {
        "dataset_version": dataset.get("version", "unknown"),
        "provenance": provenance,
        "case_count": len(case_reports),
        "top_k": top_k,
        "embedding": embeddings.status().to_dict(),
        "reranker": reranker.status().to_dict(),
        "metrics": {
            "recall_at_k": round(sum(item["recall_at_k"] for item in ranked_reports) / ranked_count, 4),
            "mrr": round(sum(item["mrr"] for item in ranked_reports) / ranked_count, 4),
            "ndcg_at_k": round(sum(item["ndcg_at_k"] for item in ranked_reports) / ranked_count, 4),
            "chunk_recall_at_k": round(sum(item["chunk_recall_at_k"] for item in chunk_reports) / chunk_count, 4) if chunk_reports else None,
            "chunk_mrr": round(sum(item["chunk_mrr"] for item in chunk_reports) / chunk_count, 4) if chunk_reports else None,
            "chunk_ndcg_at_k": round(sum(item["chunk_ndcg_at_k"] for item in chunk_reports) / chunk_count, 4) if chunk_reports else None,
            "answerability_accuracy": round(sum(bool(item["answerability_correct"]) for item in case_reports) / count, 4),
        },
        "cases": case_reports,
    }


def load_dataset(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
