from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from .answering import (
    ExtractiveAnswerProvider,
    GroundedAnswer,
    fast_factual_answer,
    filter_topic_consistent_results,
    select_evidence,
)
from .context_compression import compact_evidence_context

ModelProvider = Literal["auto", "ollama", "openai-compatible", "extractive"]
ModelState = Literal["ready", "service-unavailable", "model-missing", "extractive", "error"]
ContextDecision = Literal["independent", "follow_up", "uncertain"]


@dataclass
class ModelSettings:
    provider: ModelProvider = "auto"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:8b"
    timeout_seconds: int = 90


@dataclass
class ModelStatus:
    provider: ModelProvider
    state: ModelState
    model: str
    base_url: str
    message: str
    execution_mode: str = "auto"


@dataclass
class GroundedAnswerStream:
    citations: list[dict[str, Any]]
    suggestions: list[str]
    no_evidence: bool
    provider: str
    deltas: AsyncIterator[str]


@dataclass(frozen=True)
class QueryContextPlan:
    """Safe query plan for a conversational retrieval turn.

    ``standalone_question`` is the only question sent to the answerer.
    ``retrieval_questions`` may contain both the current wording and a rewritten
    wording only when the turn is genuinely ambiguous. It never contains a
    mechanical concatenation of two user questions.
    """

    decision: ContextDecision
    standalone_question: str
    retrieval_questions: tuple[str, ...]
    confidence: float
    use_answer_history: bool


def _prediction_budget(question: str) -> int:
    length = len(question.strip())
    if re.search(r"所有|全部|完整|逐一|罗列|列出|比较|分别", question):
        return 420
    if len([part for part in re.split(r"[？?；;]+", question) if part.strip()]) > 1:
        return 360
    if length <= 60:
        return 180
    if length <= 220:
        return 700
    return 900


def _history_text(history: list[dict[str, str]], *, max_items: int = 8, max_chars: int = 900) -> str:
    lines: list[str] = []
    for item in history[-max_items:]:
        role = "用户" if item.get("role") == "user" else "助手"
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"{role}：{content[:max_chars]}")
    return "\n".join(lines)


def _last_user_question(history: list[dict[str, str]]) -> str:
    return next(
        (
            str(item.get("content") or "").strip()
            for item in reversed(history)
            if item.get("role") == "user" and item.get("content")
        ),
        "",
    )


def _self_contained_topic_query(question: str) -> str | None:
    """Normalize follow-up-looking wording that already names its own topic."""

    normalized = re.sub(r"\s+", "", question).strip()
    direct_topic = re.fullmatch(
        r"(?:那)?(?P<topic>[\u3400-\u9fffA-Za-z0-9_-]{1,20}?)(?:问题)?(?:是)?(?:怎么|如何)规定的?[？?]?",
        normalized,
    )
    if direct_topic and direct_topic.group("topic") not in {"他", "她", "它", "这个", "该", "上述"}:
        return f"{direct_topic.group('topic').removesuffix('问题')}问题是怎么规定的？"

    explicit_topic = re.search(r"这个(?P<topic>[\u3400-\u9fffA-Za-z0-9_-]{1,20}?)(?:问题)?(?:是|的|怎|如|[？?])", normalized)
    if explicit_topic and re.search(r"规定|处理|计算|认定", normalized):
        topic = explicit_topic.group("topic").removesuffix("问题")
        predicate = "怎么规定" if "规定" in normalized else "怎么处理" if "处理" in normalized else "怎么认定"
        return f"{topic}问题是{predicate}的？"
    return None


def _initial_context_decision(question: str, history: list[dict[str, str]]) -> ContextDecision:
    """Classify dependency by question completeness, never by length alone."""

    if not history:
        return "independent"
    normalized = re.sub(r"\s+", "", question).strip()
    if _self_contained_topic_query(question):
        return "independent"
    if re.fullmatch(r"(?:是)?哪\d+(?:座|个|项|条|家)[？?]?", normalized):
        return "follow_up"
    if re.fullmatch(r"那.+?(?:呢|怎么样|如何)[？?]?", normalized):
        return "follow_up"
    if re.fullmatch(r"那(?!.*(?:哪|什么|多少|怎么|如何|是否|能否|可以|有几)).{1,12}[？?]?", normalized):
        return "follow_up"
    if re.search(r"上述|前述|前面|刚才|上面|继续|这些|那些|该标准|该规定|这个标准|这个规定", normalized):
        return "follow_up"
    if re.fullmatch(
        r"(?:那|那么)?(?:这个|那个|它|他|她|该项|该条)?(?:标准|条件|例外|原因|结果|规定|流程|处罚)?"
        r"(?:是)?(?:多少|什么|哪些|怎么|如何|为什么|呢|怎么样)[？?]?",
        normalized,
    ):
        return "uncertain"
    if normalized.endswith("呢？") or normalized.endswith("呢?"):
        return "uncertain"
    return "independent"


def query_context_needs_similarity(question: str, history: list[dict[str, str]]) -> bool:
    """Only ambiguous turns pay for a semantic topic comparison."""

    return _initial_context_decision(question, history) == "uncertain" and bool(_last_user_question(history))


def _previous_question_topic(question: str) -> str:
    topic = re.sub(r"[？?。！!]", "", question).strip()
    topic = re.sub(
        r"(?:有哪(?:些|几)(?:座|个|项)?|是哪(?:些|个)?|有哪些|是什么|怎么规定|如何规定|多少|几(?:天|个|年|次)?)$",
        "",
        topic,
    ).strip(" ，,的")
    return topic


def _deterministic_followup_query(question: str, history: list[dict[str, str]]) -> str | None:
    normalized = re.sub(r"\s+", "", question).strip()
    last_user = _last_user_question(history)

    self_contained = _self_contained_topic_query(question)
    if self_contained:
        return self_contained

    count_followup = re.fullmatch(r"(?:是)?哪(?P<count>\d+)(?P<unit>座|个|项|条|家)[？?]?", normalized)
    if count_followup and last_user:
        topic = _previous_question_topic(last_user)
        if topic:
            return f"{topic}是哪{count_followup.group('count')}{count_followup.group('unit')}？"

    replacement_match = re.fullmatch(r"那(?P<subject>.+?)(?:呢|怎么样|如何)?[？?]?", normalized)
    if replacement_match and last_user:
        replacement = replacement_match.group("subject").strip("的")
        roles = (
            "试用期员工", "部门负责人", "普通员工", "正式员工", "外包人员",
            "负责人", "实习生", "总监", "经理", "员工",
        )
        for role in roles:
            if role in last_user and role != replacement:
                return last_user.replace(role, replacement, 1)
    return None


async def _chunked_text(content: str, size: int = 24) -> AsyncIterator[str]:
    for start in range(0, len(content), size):
        yield content[start:start + size]


def validate_loopback_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("模型服务地址必须是本机 HTTP 回环地址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("模型服务地址不能包含凭据、查询参数或片段。")
    if not parsed.port:
        raise ValueError("模型服务地址必须包含端口。")
    return normalized


class ModelManager:
    def __init__(
        self,
        data_dir: Path,
        *,
        default_provider: ModelProvider = "auto",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings_path = data_dir / "model-settings.json"
        self._lock = threading.RLock()
        self._transport = transport
        self._settings = self._load(default_provider)
        self._extractive = ExtractiveAnswerProvider()
        self._status_cache: ModelStatus | None = None
        self._status_cached_at = 0.0
        self._force_cpu = False

    def _load(self, default_provider: ModelProvider) -> ModelSettings:
        defaults = ModelSettings(provider=default_provider)
        if not self.settings_path.exists():
            return defaults
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return self._validate(ModelSettings(**payload))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return defaults

    def _validate(self, settings: ModelSettings) -> ModelSettings:
        if settings.provider not in {"auto", "ollama", "openai-compatible", "extractive"}:
            raise ValueError("不支持的模型提供器。")
        model = settings.model.strip()
        if not model or len(model) > 160:
            raise ValueError("模型名称不能为空且不能超过 160 个字符。")
        timeout = max(10, min(int(settings.timeout_seconds), 300))
        base_url = validate_loopback_url(settings.base_url)
        return ModelSettings(provider=settings.provider, base_url=base_url, model=model, timeout_seconds=timeout)

    def get_settings(self) -> ModelSettings:
        with self._lock:
            return ModelSettings(**asdict(self._settings))

    def update_settings(self, settings: ModelSettings) -> ModelSettings:
        validated = self._validate(settings)
        with self._lock:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.settings_path.with_suffix(".tmp")
            temporary_path.write_text(json.dumps(asdict(validated), ensure_ascii=False, indent=2), encoding="utf-8")
            temporary_path.replace(self.settings_path)
            self._settings = validated
            self._status_cache = None
            self._status_cached_at = 0.0
            self._force_cpu = False
        return self.get_settings()

    def _remember_status(self, status: ModelStatus) -> ModelStatus:
        with self._lock:
            self._status_cache = status
            self._status_cached_at = time.monotonic()
        return status

    async def status(self, *, force: bool = False) -> ModelStatus:
        with self._lock:
            if not force and self._status_cache is not None and time.monotonic() - self._status_cached_at < 12:
                return ModelStatus(**asdict(self._status_cache))
        settings = self.get_settings()
        if settings.provider == "extractive":
            return self._remember_status(ModelStatus(
                provider=settings.provider,
                state="extractive",
                model="严格原文模式",
                base_url=settings.base_url,
                message="未启用大模型，将仅整理真实检索片段。",
            ))
        provider: ModelProvider = "ollama" if settings.provider == "auto" else settings.provider
        try:
            async with self._client(2.5) as client:
                if provider == "ollama":
                    response = await client.get(f"{settings.base_url}/api/tags")
                    response.raise_for_status()
                    models = {str(item.get("name", "")) for item in response.json().get("models", [])}
                    model_ready = settings.model in models or any(name.split(":")[0] == settings.model for name in models)
                    if not model_ready:
                        return self._remember_status(ModelStatus(provider=provider, state="model-missing", model=settings.model, base_url=settings.base_url, message="Ollama 已运行，但尚未找到所选模型。"))
                else:
                    response = await client.get(f"{settings.base_url}/v1/models")
                    response.raise_for_status()
            message = "本地模型服务连接正常。"
            if provider == "ollama" and self._force_cpu:
                message = "Ollama GPU 加载不兼容，当前使用 CPU 兼容模式运行对话模型。"
            return self._remember_status(ModelStatus(
                provider=provider,
                state="ready",
                model=settings.model,
                base_url=settings.base_url,
                message=message,
                execution_mode="cpu-compatibility" if provider == "ollama" and self._force_cpu else "auto",
            ))
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            label = "Ollama" if provider == "ollama" else "llama.cpp / OpenAI 兼容服务"
            return self._remember_status(ModelStatus(provider=provider, state="service-unavailable", model=settings.model, base_url=settings.base_url, message=f"未连接到 {label}，问答将自动使用严格原文模式。"))

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport, trust_env=False)

    async def plan_query(
        self,
        question: str,
        history: list[dict[str, str]],
        *,
        topic_similarity: float | None = None,
    ) -> QueryContextPlan:
        """Build a context-safe standalone and retrieval plan for one turn."""

        decision = _initial_context_decision(question, history)
        self_contained = _self_contained_topic_query(question)
        if decision == "independent":
            standalone = self_contained or question
            return QueryContextPlan("independent", standalone, (standalone,), 0.96, False)

        deterministic = _deterministic_followup_query(question, history)
        if deterministic:
            return QueryContextPlan("follow_up", deterministic, (deterministic,), 0.98, False)

        if decision == "uncertain" and topic_similarity is not None:
            if topic_similarity <= 0.22:
                return QueryContextPlan("independent", question, (question,), 0.88, False)
            if topic_similarity >= 0.48:
                decision = "follow_up"

        # A failed or unavailable rewriter must fall back to the current
        # question. Reintroducing the previous question here is the source of
        # cross-topic answer reuse.
        safe_fallback = QueryContextPlan(decision, question, (question,), 0.55, False)
        settings = self.get_settings()
        status = await self.status()
        if status.state != "ready":
            return safe_fallback

        prompt = (
            "请根据对话历史，把最后的追问改写成一条语义完整、可独立检索的问题。"
            "保留制度名称、对象、金额、日期、条件等限定词。只输出改写后的问题，不要回答。\n\n"
            f"对话历史：\n{_history_text(history, max_items=6, max_chars=500)}\n\n当前追问：{question}"
        )
        provider = "ollama" if settings.provider == "auto" else settings.provider
        try:
            async with self._client(min(float(settings.timeout_seconds), 45.0)) as client:
                if provider == "ollama":
                    options: dict[str, object] = {"temperature": 0, "num_predict": 96}
                    if self._force_cpu:
                        options["num_gpu"] = 0
                    response = await client.post(
                        f"{settings.base_url}/api/chat",
                        json={
                            "model": settings.model,
                            "stream": False,
                            "think": False,
                            "keep_alive": "15m",
                            "messages": [{"role": "user", "content": prompt}],
                            "options": options,
                        },
                    )
                    response.raise_for_status()
                    rewritten = str(response.json().get("message", {}).get("content", "")).strip()
                else:
                    response = await client.post(
                        f"{settings.base_url}/v1/chat/completions",
                        json={
                            "model": settings.model,
                            "stream": False,
                            "temperature": 0,
                            "max_tokens": 96,
                            "messages": [{"role": "user", "content": prompt}],
                        },
                    )
                    response.raise_for_status()
                    rewritten = str(response.json()["choices"][0]["message"]["content"]).strip()
            rewritten = re.sub(r"^(改写后的问题|问题)[:：]\s*", "", rewritten).strip().strip('"“”')
            if not 2 <= len(rewritten) <= 2000:
                return safe_fallback
            retrieval_questions = (rewritten,)
            if decision == "uncertain" and rewritten != question:
                retrieval_questions = (question, rewritten)
            return QueryContextPlan(decision, rewritten, retrieval_questions, 0.84, False)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            return safe_fallback

    async def rewrite_query(self, question: str, history: list[dict[str, str]]) -> str:
        """Compatibility wrapper returning the safe standalone question."""

        return (await self.plan_query(question, history)).standalone_question

    def _answer_prompts(
        self,
        question: str,
        evidence: list[dict[str, Any]],
        history: list[dict[str, str]],
    ) -> tuple[str, str]:
        context = compact_evidence_context(question, evidence, max_chars=1800)
        answer_requirements: list[str] = []
        if re.search(r"所有|全部|完整|逐一|罗列|列出|哪些", question):
            answer_requirements.append("完整列出资料中所有匹配项，禁止用‘等’‘等等’省略；若资料本身不完整，明确指出缺口。")
        if re.search(r"怎么规定|如何规定|有哪些规定|完整规则", question):
            answer_requirements.append("这是规则总览问题，应覆盖资料中的全部条件档位、累计规则和直接后果，不能只回答其中一个示例。")
        if len([item for item in re.split(r"[？?；;]+", question) if item.strip()]) > 1:
            answer_requirements.append("按当前问题的每个子问题逐项回答，不能只回答其中一项。")
        if re.search(r"能否|可否|可以|能不能|可不可以|是否|有没有|有无|吗[？?]?$", question):
            answer_requirements.append("先直接给出允许、禁止、可以或不可以的结论，再说明条件、例外和相关后果。")
        if re.search(r"差多少|相差|超过|不足|以内|以上|以下|能不能报|能否报|可以报|有几天", question):
            answer_requirements.append(
                "先把用户给出的金额、年限、城市、职级等条件与资料中的同一表格行或阈值逐项绑定，再做比较或计算；"
                "不得把其他城市、其他职级或相邻行的数字混入答案。"
            )
        requirement_text = "".join(answer_requirements)
        system_prompt = (
            "你是知域本地知识库助手。只能依据给出的本地资料回答，不得使用常识补全或猜测。"
            "对话历史只用于理解指代和追问，不是事实依据。每个事实句末必须标注对应引用 [[1]]、[[2]]。"
            "首句应在40字内直接给出第一个结论，并在句末立即标注引用；不要先写背景、分析过程或复述问题。"
            "涉及金额、日期、天数、比例、职级或审批人时必须逐字核对原文。资料不足或相互冲突时明确说明无法确认。"
            "禁止复述文档标题、章节目录、原始 Markdown 表格或分隔线，禁止把检索资料原样抄写成答案。"
            f"{requirement_text}使用简洁中文和 Markdown，不要输出参考资料列表之外的链接。"
        )
        if len([part for part in re.split(r"[？?；;]+", question) if part.strip()]) > 1:
            system_prompt += "当前问题包含多项诉求，必须按原顺序逐项回答，不得因先找到其中一项就省略其余问题。"
        history_block = _history_text(history)
        user_prompt = (
            (f"对话历史：\n{history_block}\n\n" if history_block else "")
            + f"当前问题：{question}\n\n本地检索资料：\n{context}\n\n请直接回答当前问题：{question}\n只输出最终答案，不要复述资料。"
        )
        return system_prompt, user_prompt

    async def stream(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        history: list[dict[str, str]] | None = None,
    ) -> GroundedAnswerStream:
        history = history or []
        search_results = filter_topic_consistent_results(question, search_results)
        fallback = self._extractive.build(question, search_results)
        if fallback.no_evidence:
            return GroundedAnswerStream(
                citations=fallback.citations,
                suggestions=fallback.suggestions,
                no_evidence=True,
                provider=fallback.provider,
                deltas=_chunked_text(fallback.content),
            )
        evidence = select_evidence(question, search_results)
        fast_answer = fast_factual_answer(question, evidence)
        if fast_answer:
            return GroundedAnswerStream(
                citations=fallback.citations,
                suggestions=fallback.suggestions,
                no_evidence=False,
                provider="extractive-fast-v1",
                deltas=_chunked_text(fast_answer),
            )
        settings = self.get_settings()
        status = await self.status()
        if status.state != "ready":
            return GroundedAnswerStream(
                citations=fallback.citations,
                suggestions=fallback.suggestions,
                no_evidence=False,
                provider=fallback.provider,
                deltas=_chunked_text(fallback.content),
            )

        system_prompt, user_prompt = self._answer_prompts(question, evidence, history)
        provider = "ollama" if settings.provider == "auto" else settings.provider
        valid_markers = {f"[[{index}]]" for index in range(1, len(fallback.citations) + 1)}
        generation_state = {"failed": False}

        async def ollama_deltas() -> AsyncIterator[str]:
            use_cpu = self._force_cpu
            while True:
                options: dict[str, object] = {"temperature": 0.1, "num_predict": _prediction_budget(question), "num_ctx": 4096}
                if use_cpu:
                    options["num_gpu"] = 0
                payload = {
                    "model": settings.model,
                    "stream": True,
                    "think": False,
                    "keep_alive": "15m",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "options": options,
                }
                try:
                    async with self._client(float(settings.timeout_seconds)) as client:
                        async with client.stream("POST", f"{settings.base_url}/api/chat", json=payload) as response:
                            if response.status_code >= 400:
                                error_text = (await response.aread()).decode("utf-8", errors="replace").lower()
                                cuda_incompatible = response.status_code == 500 and (
                                    "cuda error" in error_text or "unsupported toolchain" in error_text
                                )
                                if not use_cpu and cuda_incompatible:
                                    self._force_cpu = True
                                    self._status_cache = None
                                    self._status_cached_at = 0.0
                                    use_cpu = True
                                    continue
                                response.raise_for_status()
                            async for line in response.aiter_lines():
                                if not line.strip():
                                    continue
                                event = json.loads(line)
                                delta = str(event.get("message", {}).get("content", ""))
                                if delta:
                                    yield delta
                    break
                except (httpx.HTTPError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    generation_state["failed"] = True
                    return

        async def openai_deltas() -> AsyncIterator[str]:
            try:
                async with self._client(float(settings.timeout_seconds)) as client:
                    async with client.stream(
                        "POST",
                        f"{settings.base_url}/v1/chat/completions",
                        json={
                            "model": settings.model,
                            "stream": True,
                            "temperature": 0.1,
                            "max_tokens": _prediction_budget(question),
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                        },
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                break
                            event = json.loads(payload)
                            delta = str(event.get("choices", [{}])[0].get("delta", {}).get("content") or "")
                            if delta:
                                yield delta
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
                generation_state["failed"] = True
                return

        async def retry_invalid_answer(reason: str) -> str | None:
            retry_system = (
                system_prompt
                + "上一版回答未通过本地校验。请重新生成一个更短的最终答案：先给结论，最多三句话，"
                "每句话都必须带有效引用；不要输出标题、表格、原文块或解释校验过程。"
            )
            retry_user = f"{user_prompt}\n\n失败原因：{reason}。请只输出修正后的最终答案。"
            try:
                async with self._client(min(float(settings.timeout_seconds), 45.0)) as client:
                    if provider == "ollama":
                        options: dict[str, object] = {"temperature": 0, "num_predict": min(240, _prediction_budget(question)), "num_ctx": 4096}
                        if self._force_cpu:
                            options["num_gpu"] = 0
                        response = await client.post(
                            f"{settings.base_url}/api/chat",
                            json={
                                "model": settings.model,
                                "stream": False,
                                "think": False,
                                "keep_alive": "15m",
                                "messages": [
                                    {"role": "system", "content": retry_system},
                                    {"role": "user", "content": retry_user},
                                ],
                                "options": options,
                            },
                        )
                        response.raise_for_status()
                        content = str(response.json().get("message", {}).get("content", "")).strip()
                    else:
                        response = await client.post(
                            f"{settings.base_url}/v1/chat/completions",
                            json={
                                "model": settings.model,
                                "stream": False,
                                "temperature": 0,
                                "max_tokens": min(240, _prediction_budget(question)),
                                "messages": [
                                    {"role": "system", "content": retry_system},
                                    {"role": "user", "content": retry_user},
                                ],
                            },
                        )
                        response.raise_for_status()
                        content = str(response.json()["choices"][0]["message"]["content"]).strip()
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
                return None
            markers = set(re.findall(r"\[\[\d+]]", content))
            context_dump = content.lstrip().startswith(("#", "---", "|")) or content.count("|") >= 8
            if not content or context_dump or not markers or not markers <= valid_markers:
                return None
            return content

        async def validated_deltas(source: AsyncIterator[str]) -> AsyncIterator[str]:
            buffer = ""
            released = False
            async for delta in source:
                if released:
                    yield delta
                    continue
                buffer += delta
                stripped = buffer.lstrip()
                context_dump = (
                    stripped.startswith(("#", "---", "|"))
                    or buffer.count("|") >= 8
                    or buffer.count("\n---") >= 2
                )
                if context_dump:
                    repaired = await retry_invalid_answer("回答包含原始表格或文档转储")
                    if repaired:
                        async for repaired_delta in _chunked_text(repaired):
                            yield repaired_delta
                        return
                    async for fallback_delta in _chunked_text(fallback.content):
                        yield fallback_delta
                    return
                if len(buffer) > max(720, _prediction_budget(question) * 3):
                    repaired = await retry_invalid_answer("回答过长且没有及时提供有效引用")
                    if repaired:
                        async for repaired_delta in _chunked_text(repaired):
                            yield repaired_delta
                        return
                    async for fallback_delta in _chunked_text(fallback.content):
                        yield fallback_delta
                    return
                markers = set(re.findall(r"\[\[\d+]]", buffer))
                if markers and markers <= valid_markers:
                    released = True
                    yield buffer
                    buffer = ""
            if released:
                return
            markers = set(re.findall(r"\[\[\d+]]", buffer))
            if buffer.strip() and markers and markers <= valid_markers:
                yield buffer
                return
            if generation_state["failed"] and not buffer.strip():
                async for fallback_delta in _chunked_text(fallback.content):
                    yield fallback_delta
                return
            repaired = await retry_invalid_answer("回答缺少有效引用或未完整生成")
            if repaired:
                async for repaired_delta in _chunked_text(repaired):
                    yield repaired_delta
                return
            async for fallback_delta in _chunked_text(fallback.content):
                yield fallback_delta

        raw_deltas = ollama_deltas() if provider == "ollama" else openai_deltas()
        return GroundedAnswerStream(
            citations=fallback.citations,
            suggestions=fallback.suggestions,
            no_evidence=False,
            provider=f"{provider}:{settings.model}",
            deltas=validated_deltas(raw_deltas),
        )

    async def build(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        history: list[dict[str, str]] | None = None,
    ) -> GroundedAnswer:
        search_results = filter_topic_consistent_results(question, search_results)
        fallback = self._extractive.build(question, search_results)
        if fallback.no_evidence:
            return fallback
        settings = self.get_settings()
        status = await self.status()
        if status.state != "ready":
            return fallback

        evidence = select_evidence(question, search_results)
        system_prompt, user_prompt = self._answer_prompts(question, evidence, history or [])
        try:
            provider = "ollama" if settings.provider == "auto" else settings.provider
            async with self._client(float(settings.timeout_seconds)) as client:
                if provider == "ollama":
                    options: dict[str, object] = {"temperature": 0.1, "num_predict": _prediction_budget(question), "num_ctx": 4096}
                    if self._force_cpu:
                        options["num_gpu"] = 0
                    payload = {
                        "model": settings.model,
                        "stream": False,
                        "think": False,
                        "keep_alive": "15m",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "options": options,
                    }
                    response = await client.post(f"{settings.base_url}/api/chat", json=payload)
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError:
                        error_text = response.text.lower()
                        cuda_incompatible = response.status_code == 500 and (
                            "cuda error" in error_text or "unsupported toolchain" in error_text
                        )
                        if self._force_cpu or not cuda_incompatible:
                            raise
                        self._force_cpu = True
                        payload["options"] = {**options, "num_gpu": 0}
                        response = await client.post(f"{settings.base_url}/api/chat", json=payload)
                        response.raise_for_status()
                        self._status_cache = None
                        self._status_cached_at = 0.0
                    content = str(response.json().get("message", {}).get("content", "")).strip()
                else:
                    response = await client.post(
                        f"{settings.base_url}/v1/chat/completions",
                        json={
                            "model": settings.model,
                            "stream": False,
                            "temperature": 0.1,
                            "max_tokens": _prediction_budget(question),
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                        },
                    )
                    response.raise_for_status()
                    content = str(response.json()["choices"][0]["message"]["content"]).strip()
            if not content or not re.search(r"\[\[\d+]]", content):
                return fallback
            valid_ids = {f"[[{index}]]" for index in range(1, len(fallback.citations) + 1)}
            if any(marker not in valid_ids for marker in re.findall(r"\[\[\d+]]", content)):
                return fallback
            return GroundedAnswer(
                content=content,
                citations=fallback.citations,
                suggestions=fallback.suggestions,
                no_evidence=False,
                provider=f"{provider}:{settings.model}",
            )
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            return fallback
