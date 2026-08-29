from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from .context_compression import compact_evidence_text
from .retrieval_planning import alternate_questions

MIN_EVIDENCE_SCORE = 0.45
MAX_EVIDENCE_ITEMS = 6


def evidence_score(item: dict[str, Any]) -> float:
    return float(item.get("evidence_score", item.get("score", 0.0)))


def select_evidence(question: str, search_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    complex_question = (
        len([part for part in re.split(r"[？?；;]+", question) if part.strip()]) > 1
        or bool(re.search(r"所有|全部|完整|逐一|罗列|列出|比较|分别", question))
    )
    limit = MAX_EVIDENCE_ITEMS if complex_question else 4
    return [item for item in search_results if evidence_score(item) >= MIN_EVIDENCE_SCORE][:limit]


def fast_factual_answer(question: str, evidence: list[dict[str, Any]]) -> str | None:
    """Answer narrow, high-confidence structured time lookups without an LLM."""

    if not re.search(r"上班时间|工作时间|办公时间|几点上班|几点下班", question):
        return None
    subject = re.split(r"上班时间|工作时间|办公时间|几点上班|几点下班", question, maxsplit=1)[0]
    subject = re.sub(r"^(?:请问|查询|查一下|我想知道)", "", subject).strip(" ，,。？?")
    for index, item in enumerate(evidence, start=1):
        compacted = compact_evidence_text(question, item, 420)
        for line in compacted.splitlines():
            if subject and subject not in line:
                continue
            times = re.findall(r"(?<!\d)([0-2]?\d:\d{2})(?!\d)", line)
            if len(times) < 2:
                continue
            label = subject or "该地点"
            answer = f"{label}的工作时间为 **{times[0]}–{times[1]}**"
            if len(times) >= 4:
                answer += f"，午休时间为 **{times[2]}–{times[3]}**"
            return f"{answer}。 [[{index}]]"
    return None


@dataclass
class GroundedAnswer:
    content: str
    citations: list[dict[str, Any]]
    suggestions: list[str]
    no_evidence: bool
    provider: str


class AnswerProvider(Protocol):
    async def build(self, question: str, search_results: list[dict[str, Any]]) -> GroundedAnswer: ...


def _excerpt(content: str, limit: int = 340) -> str:
    normalized = re.sub(r"\s+", " ", content).strip().lstrip("#- ")
    if len(normalized) <= limit:
        return normalized
    boundary = max(normalized.rfind("。", 0, limit), normalized.rfind("；", 0, limit), normalized.rfind(". ", 0, limit))
    return normalized[:boundary + 1 if boundary > limit // 2 else limit].rstrip() + "…"


class ExtractiveAnswerProvider:
    """Grounded local fallback used until an optional LLM provider is configured."""

    provider_id = "extractive-local-v1"

    def build(self, question: str, search_results: list[dict[str, Any]]) -> GroundedAnswer:
        evidence = select_evidence(question, search_results)
        if not evidence:
            return GroundedAnswer(
                content=(
                    "当前知识库中没有找到足够可靠的依据来回答这个问题。\n\n"
                    "你可以尝试补充更具体的关键词、切换知识库，或先在文档管理中上传相关资料。"
                    "为了避免误导，我不会在缺少依据时推测答案。"
                ),
                citations=[],
                suggestions=alternate_questions(question),
                no_evidence=True,
                provider=self.provider_id,
            )

        citations = []
        answer_lines = ["根据当前知识库检索到的原文，相关内容如下：", ""]
        headings: list[str] = []
        for index, item in enumerate(evidence, start=1):
            excerpt = _excerpt(compact_evidence_text(question, item, 340))
            answer_lines.append(f"- {excerpt} [[{index}]]")
            heading = str(item.get("heading") or "").strip()
            if heading and heading not in headings:
                headings.append(heading)
            citations.append(
                {
                    "id": index,
                    "chunk_id": item["chunk_id"],
                    "document_id": item["document_id"],
                    "document_name": item["document_name"],
                    "format": item["format"],
                    "page": item.get("page"),
                    "chapter": heading or "正文",
                    "updated_at": item["updated_at"],
                    "excerpt": excerpt,
                    "context": item.get("parent_content") or item["content"],
                    "parent_chunk_id": item.get("parent_chunk_id"),
                    "content_kind": item.get("content_kind", "text"),
                    "location": item.get("location"),
                    "score": evidence_score(item),
                }
            )
        answer_lines.extend(["", "以上内容直接来自本地知识库的检索结果。当前尚未启用大模型归纳，因此保留原文表述，避免添加资料中不存在的信息。"])
        suggestions = [f"继续查看“{heading}”" for heading in headings[:2]]
        suggestions.extend(["概括这些资料的关键要点", "比较不同来源的相关内容"])
        return GroundedAnswer(
            content="\n".join(answer_lines),
            citations=citations,
            suggestions=suggestions[:3],
            no_evidence=False,
            provider=self.provider_id,
        )
