from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Literal, cast

from .embeddings import EmbeddingManager, EmbeddingProviderName
from .ingestion import DocumentProcessor
from .modeling import ModelManager, query_context_needs_similarity
from .reranking import RerankerManager
from .retrieval_planning import build_retrieval_queries
from .storage import KnowledgeStore

ExperienceMode = Literal["retrieval", "answer", "all"]
_VARIANT_KINDS = {"equivalent", "contrast"}


def default_app_data_dir() -> Path:
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA")
        if not roaming:
            raise ValueError("无法确定 Windows APPDATA 目录")
        return Path(roaming) / "com.zhiyu.knowledgehub"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "com.zhiyu.knowledgehub"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "com.zhiyu.knowledgehub"


def validate_experience_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("type") != "local-user-feedback":
        raise ValueError("体验回归清单必须声明 provenance.type=local-user-feedback")
    if provenance.get("contains_document_content") is not False:
        raise ValueError("体验回归清单必须明确 contains_document_content=false")
    if "documents" in payload:
        raise ValueError("体验回归清单不得包含原始文档正文")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("体验回归清单至少需要一个案例")
    identifiers: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or not str(case.get("id") or "").strip():
            raise ValueError("每个体验回归案例都必须包含 id")
        identifier = str(case["id"])
        if identifier in identifiers:
            raise ValueError(f"体验回归案例 id 重复：{identifier}")
        identifiers.add(identifier)
        if not str(case.get("query") or "").strip():
            raise ValueError(f"体验回归案例缺少 query：{identifier}")
        group_id = str(case.get("paraphrase_group_id") or "").strip()
        variant_kind = str(case.get("variant_kind") or "").strip()
        if group_id and variant_kind not in _VARIANT_KINDS:
            raise ValueError(f"表达分组案例必须声明 variant_kind：{identifier}")
        if variant_kind and not group_id:
            raise ValueError(f"variant_kind 必须与 paraphrase_group_id 同时使用：{identifier}")
        for field_name in ("required_query_constraints", "forbidden_query_terms", "prohibited_sources"):
            value = case.get(field_name, [])
            if not isinstance(value, list):
                raise ValueError(f"{field_name} 必须是数组：{identifier}")
    gates = payload.get("quality_gates", {})
    if not isinstance(gates, dict):
        raise ValueError("quality_gates 必须是对象")
    for name, value in gates.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= float(value) <= 1:
            raise ValueError(f"质量门禁 {name} 必须是 0 到 1 之间的数字")
    return payload


def load_experience_manifest(path: Path) -> dict[str, Any]:
    """Load a manifest and merge explicitly listed sibling manifests."""

    resolved_path = path.resolve()
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    includes = payload.pop("includes", [])
    if not isinstance(includes, list):
        raise ValueError("includes 必须是数组")
    inherited_cases: list[dict[str, Any]] = []
    for item in includes:
        include_path = (resolved_path.parent / str(item)).resolve()
        if include_path.parent != resolved_path.parent or include_path == resolved_path:
            raise ValueError(f"体验回归 includes 只能引用同目录文件：{item}")
        inherited = load_experience_manifest(include_path)
        inherited_cases.extend(inherited["cases"])
    payload["cases"] = [*inherited_cases, *payload.get("cases", [])]
    return validate_experience_manifest(payload)


def snapshot_knowledge_data(source_data_dir: Path, destination_data_dir: Path) -> Path:
    source_database = source_data_dir.resolve() / "knowledge.db"
    if not source_database.is_file():
        raise FileNotFoundError(f"未找到本地知识库：{source_database}")
    destination_data_dir.mkdir(parents=True, exist_ok=True)
    destination_database = destination_data_dir / "knowledge.db"
    source_uri = f"{source_database.as_uri()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=20) as source:
        with sqlite3.connect(destination_database, timeout=20) as destination:
            source.backup(destination)
    settings_path = source_data_dir / "model-settings.json"
    if settings_path.is_file():
        shutil.copy2(settings_path, destination_data_dir / settings_path.name)
    return destination_database


def _normalized(value: str) -> str:
    compact = "".join(unicodedata.normalize("NFKC", value).lower().split())
    return compact.translate(str.maketrans("", "", "*_`"))


def _missing_terms(content: str, terms: list[object]) -> list[str]:
    normalized = _normalized(content)
    return [str(term) for term in terms if _normalized(str(term)) not in normalized]


def _matching_provider(database_path: Path, requested: EmbeddingProviderName) -> EmbeddingProviderName:
    if requested != "auto":
        return requested
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT DISTINCT embedding_model_id FROM chunks WHERE embedding_model_id IS NOT NULL"
        ).fetchall()
    model_ids = {str(row[0]) for row in rows}
    if any(model_id.startswith("ollama:") for model_id in model_ids):
        return "ollama"
    return "hashing"


def _retrieval_queries(roots: tuple[str, ...]) -> list[str]:
    queries: list[str] = []
    for root in roots:
        for query in build_retrieval_queries(root):
            if query not in queries:
                queries.append(query)
            if len(queries) >= 5:
                return queries
    return queries


def _constraint_is_present(query: str, alternatives: object) -> bool:
    if not isinstance(alternatives, list) or not alternatives:
        return False
    normalized_query = _normalized(query)
    return any(_normalized(str(term)) in normalized_query for term in alternatives)


def _pairwise_jaccard(sets: list[set[str]]) -> float:
    scores: list[float] = []
    for left_index, left in enumerate(sets):
        for right in sets[left_index + 1:]:
            union = left | right
            scores.append(len(left & right) / len(union) if union else 1.0)
    return sum(scores) / len(scores) if scores else 1.0


def build_expression_metrics(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize paraphrase stability without reading or storing document bodies."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for report in reports:
        group_id = str(report.get("paraphrase_group_id") or "")
        if group_id:
            grouped.setdefault(group_id, []).append(report)

    equivalent_cases = [
        report for items in grouped.values() for report in items
        if report.get("variant_kind") == "equivalent"
    ]
    contrast_cases = [
        report for items in grouped.values() for report in items
        if report.get("variant_kind") == "contrast"
    ]
    source_scores: list[float] = []
    group_reports: list[dict[str, Any]] = []
    for group_id, items in grouped.items():
        equivalents = [item for item in items if item.get("variant_kind") == "equivalent"]
        relevant_source_sets = [set(item.get("matched_expected_sources", [])) for item in equivalents]
        source_consistency = _pairwise_jaccard(relevant_source_sets)
        if len(equivalents) >= 2:
            source_scores.append(source_consistency)
        group_reports.append({
            "id": group_id,
            "equivalent_count": len(equivalents),
            "contrast_count": len(items) - len(equivalents),
            "expression_consistency_rate": round(
                sum(bool(item.get("retrieval_passed")) for item in equivalents) / max(1, len(equivalents)), 4
            ),
            "source_consistency_rate": round(source_consistency, 4),
            "query_drift_count": sum(bool(item.get("query_drift")) for item in items),
        })

    return {
        "group_count": len(grouped),
        "equivalent_case_count": len(equivalent_cases),
        "contrast_case_count": len(contrast_cases),
        "expression_consistency_rate": round(
            sum(bool(item.get("retrieval_passed")) for item in equivalent_cases) / max(1, len(equivalent_cases)), 4
        ),
        "source_consistency_rate": round(sum(source_scores) / max(1, len(source_scores)), 4),
        "query_drift_rate": round(
            sum(bool(item.get("query_drift")) for item in equivalent_cases + contrast_cases)
            / max(1, len(equivalent_cases) + len(contrast_cases)),
            4,
        ),
        "contrast_accuracy_rate": round(
            sum(bool(item.get("retrieval_passed")) for item in contrast_cases) / max(1, len(contrast_cases)), 4
        ),
        "groups": group_reports,
    }


def evaluate_quality_gates(metrics: dict[str, Any], gates: dict[str, Any]) -> list[str]:
    checks = {
        "min_expression_consistency_rate": ("expression_consistency_rate", "表达一致率", "min"),
        "min_source_consistency_rate": ("source_consistency_rate", "来源一致率", "min"),
        "min_contrast_accuracy_rate": ("contrast_accuracy_rate", "意义变化反例准确率", "min"),
        "max_query_drift_rate": ("query_drift_rate", "查询漂移率", "max"),
    }
    failures: list[str] = []
    for gate_name, (metric_name, label, direction) in checks.items():
        if gate_name not in gates:
            continue
        expected = float(gates[gate_name])
        actual = float(metrics.get(metric_name, 0.0))
        failed = actual < expected if direction == "min" else actual > expected
        if failed:
            operator = ">=" if direction == "min" else "<="
            failures.append(f"{label}应{operator}{expected:.4f}，实际为 {actual:.4f}")
    return failures


async def run_experience_evaluation(
    manifest: dict[str, Any],
    data_dir: Path,
    *,
    mode: ExperienceMode = "all",
    embedding_provider: EmbeddingProviderName = "auto",
    embedding_base_url: str = "http://127.0.0.1:11434",
    embedding_model: str = "bge-m3",
    case_ids: set[str] | None = None,
    include_answers: bool = False,
) -> dict[str, Any]:
    manifest = validate_experience_manifest(manifest)
    store = KnowledgeStore(data_dir)
    resolved_provider = _matching_provider(store.database_path, embedding_provider)
    embeddings = EmbeddingManager(
        cast(EmbeddingProviderName, resolved_provider),
        base_url=embedding_base_url,
        model=embedding_model,
    )
    reranker = RerankerManager("feature")
    processor = DocumentProcessor(store, embeddings, reranker)
    model_manager = ModelManager(data_dir)
    knowledge_base_id = str(manifest.get("knowledge_base_id") or "enterprise")
    defaults = manifest.get("default_constraints") if isinstance(manifest.get("default_constraints"), dict) else {}
    reports: list[dict[str, Any]] = []
    started_at = time.perf_counter()

    selected_cases = [case for case in manifest["cases"] if not case_ids or str(case["id"]) in case_ids]
    if case_ids:
        missing_case_ids = case_ids - {str(case["id"]) for case in selected_cases}
        if missing_case_ids:
            raise ValueError(f"未找到体验回归案例：{sorted(missing_case_ids)}")

    for case in selected_cases:
        case_started_at = time.perf_counter()
        query = str(case["query"]).strip()
        constraints = {**defaults, **case}
        history = [
            {"role": str(item.get("role") or ""), "content": str(item.get("content") or "")}
            for item in case.get("history", [])
            if isinstance(item, dict)
        ]
        topic_similarity: float | None = None
        if query_context_needs_similarity(query, history):
            previous = next((item["content"] for item in reversed(history) if item["role"] == "user"), "")
            if previous:
                topic_similarity = embeddings.similarity(query, previous)
        plan = await model_manager.plan_query(query, history, topic_similarity=topic_similarity)
        queries = _retrieval_queries(plan.retrieval_questions)
        results = processor.search_many(queries, [knowledge_base_id], 12)
        returned_sources = list(dict.fromkeys(str(item["document_name"]) for item in results))
        failures: list[str] = []
        drift_reasons: list[str] = []

        expected_decision = case.get("expected_context_decision")
        if expected_decision and plan.decision != expected_decision:
            failures.append(f"上下文判定应为 {expected_decision}，实际为 {plan.decision}")
        expected_rewrite = case.get("expected_rewritten_query")
        if expected_rewrite and _normalized(plan.standalone_question) != _normalized(str(expected_rewrite)):
            failures.append(f"独立问题不匹配：{plan.standalone_question}")

        expected_sources = [str(item) for item in case.get("expected_sources", [])]
        minimum_source_score = float(constraints.get("min_expected_source_score") or 0.0)
        source_scores = {
            source: max(
                (float(item.get("evidence_score", item.get("score", 0.0))) for item in results if str(item["document_name"]) == source),
                default=0.0,
            )
            for source in expected_sources
        }
        matched: list[str] = []
        if expected_sources:
            matched = [
                source for source in expected_sources
                if source in returned_sources and source_scores[source] >= minimum_source_score
            ]
            source_match = str(case.get("source_match") or "any")
            if source_match == "all" and len(matched) != len(expected_sources):
                failures.append(
                    f"缺少达到证据阈值 {minimum_source_score:.2f} 的预期来源："
                    f"{[source for source in expected_sources if source not in matched]}"
                )
            elif source_match != "all" and not matched:
                failures.append(f"未召回达到证据阈值 {minimum_source_score:.2f} 的预期来源：{expected_sources}")

        expanded_queries = queries[1:]
        for alternatives in case.get("required_query_constraints", []):
            missing_in = [item for item in expanded_queries if not _constraint_is_present(item, alternatives)]
            if missing_in:
                drift_reasons.append(f"扩展查询丢失约束 {alternatives}：{missing_in}")
        for term in case.get("forbidden_query_terms", []):
            contaminated = [item for item in queries if _normalized(str(term)) in _normalized(item)]
            if contaminated:
                drift_reasons.append(f"查询混入禁用语义“{term}”：{contaminated}")
        prohibited_sources = [str(item) for item in case.get("prohibited_sources", [])]
        returned_prohibited_sources = [source for source in prohibited_sources if source in returned_sources]
        if returned_prohibited_sources:
            drift_reasons.append(f"召回禁止来源：{returned_prohibited_sources}")
        if drift_reasons:
            failures.extend(drift_reasons)
        retrieval_passed = not failures

        provider = "not-run"
        citation_sources: list[str] = []
        answer_chars = 0
        if mode in {"answer", "all"}:
            answer = await model_manager.stream(
                plan.standalone_question,
                results,
                history if plan.use_answer_history else [],
            )
            content = "".join([delta async for delta in answer.deltas])
            provider = answer.provider
            answer_chars = len(content)
            citation_sources = list(dict.fromkeys(str(item["document_name"]) for item in answer.citations))
            missing = _missing_terms(content, list(constraints.get("expected_terms", [])))
            if missing:
                failures.append(f"答案缺少关键词：{missing}")
            for alternatives in constraints.get("expected_any_terms", []):
                if isinstance(alternatives, list) and len(_missing_terms(content, alternatives)) == len(alternatives):
                    failures.append(f"答案未命中任一同义词：{alternatives}")
            prohibited = [*defaults.get("prohibited_patterns", []), *case.get("prohibited_terms", [])]
            present_prohibited = [term for term in prohibited if not _missing_terms(content, [term])]
            if present_prohibited:
                failures.append(f"答案包含禁用内容：{present_prohibited}")
            history_leaks = [
                term for term in case.get("prohibited_history_terms", [])
                if not _missing_terms(content, [term])
            ]
            if history_leaks:
                failures.append(f"答案混入上一题内容：{history_leaks}")
            if bool(constraints.get("citation_required")) and (not answer.citations or "[[" not in content):
                failures.append("答案缺少有效引用")
            max_answer_chars = int(constraints.get("max_answer_chars") or 0)
            if max_answer_chars and len(content) > max_answer_chars:
                failures.append(f"答案过长：{len(content)} > {max_answer_chars}")

        case_report = {
            "id": str(case["id"]),
            "query": query,
            "passed": not failures,
            "retrieval_passed": retrieval_passed,
            "failures": failures,
            "paraphrase_group_id": case.get("paraphrase_group_id"),
            "variant_kind": case.get("variant_kind"),
            "query_drift": bool(drift_reasons),
            "query_drift_reasons": drift_reasons,
            "context_decision": plan.decision,
            "standalone_question": plan.standalone_question,
            "retrieval_queries": queries,
            "returned_sources": returned_sources[:8],
            "matched_expected_sources": matched,
            "expected_source_scores": {key: round(value, 4) for key, value in source_scores.items()},
            "provider": provider,
            "citation_sources": citation_sources,
            "answer_chars": answer_chars,
            "latency_ms": round((time.perf_counter() - case_started_at) * 1000),
        }
        if include_answers and mode in {"answer", "all"}:
            case_report["answer"] = content
        reports.append(case_report)

    passed = sum(bool(item["passed"]) for item in reports)
    expression_metrics = build_expression_metrics(reports)
    quality_gates = manifest.get("quality_gates") if isinstance(manifest.get("quality_gates"), dict) else {}
    quality_gate_failures = evaluate_quality_gates(expression_metrics, quality_gates)
    return {
        "manifest_version": manifest.get("version", "unknown"),
        "mode": mode,
        "case_count": len(reports),
        "passed": passed,
        "failed": len(reports) - passed,
        "pass_rate": round(passed / max(1, len(reports)), 4),
        "embedding": embeddings.status().to_dict(),
        "elapsed_ms": round((time.perf_counter() - started_at) * 1000),
        "expression_metrics": expression_metrics,
        "quality_gates": quality_gates,
        "quality_gates_passed": not quality_gate_failures,
        "quality_gate_failures": quality_gate_failures,
        "cases": reports,
    }


def run_experience_evaluation_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_experience_evaluation(*args, **kwargs))
