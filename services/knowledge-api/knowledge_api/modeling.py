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

from .answering import ExtractiveAnswerProvider, GroundedAnswer, fast_factual_answer, select_evidence
from .context_compression import compact_evidence_context

ModelProvider = Literal["auto", "ollama", "openai-compatible", "extractive"]
ModelState = Literal["ready", "service-unavailable", "model-missing", "extractive", "error"]


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


def _needs_query_rewrite(question: str, history: list[dict[str, str]]) -> bool:
    if not history:
        return False
    normalized = re.sub(r"\s+", "", question)
    followup_markers = ("那", "那么", "这个", "这些", "上述", "前面", "继续", "还有", "分别", "具体", "为什么", "呢")
    return len(normalized) <= 48 or normalized.startswith(followup_markers) or normalized.endswith("呢")


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

    async def rewrite_query(self, question: str, history: list[dict[str, str]]) -> str:
        """Turn a context-dependent follow-up into a standalone retrieval query."""

        if not _needs_query_rewrite(question, history):
            return question
        last_user = next(
            (str(item.get("content") or "").strip() for item in reversed(history) if item.get("role") == "user" and item.get("content")),
            "",
        )
        deterministic_fallback = f"{last_user}\n追问：{question}".strip() if last_user else question
        # Short follow-ups usually introduce one concrete replacement subject
        # (for example “那部门负责人呢”). Combining them with the previous
        # question is both reliable for hybrid retrieval and avoids a full 8B
        # model call before the user can see the first answer token.
        if len(re.sub(r"\s+", "", question)) <= 24:
            return deterministic_fallback
        settings = self.get_settings()
        status = await self.status()
        if status.state != "ready":
            return deterministic_fallback

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
            return rewritten if 2 <= len(rewritten) <= 2000 else deterministic_fallback
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            return deterministic_fallback

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
        if len([item for item in re.split(r"[？?；;]+", question) if item.strip()]) > 1:
            answer_requirements.append("按当前问题的每个子问题逐项回答，不能只回答其中一项。")
        if re.search(r"能否|可否|可以|能不能|可不可以|是否|有没有|有无|吗[？?]?$", question):
            answer_requirements.append("先直接给出允许、禁止、可以或不可以的结论，再说明条件、例外和相关后果。")
        requirement_text = "".join(answer_requirements)
        system_prompt = (
            "你是知域本地知识库助手。只能依据给出的本地资料回答，不得使用常识补全或猜测。"
            "对话历史只用于理解指代和追问，不是事实依据。每个事实句末必须标注对应引用 [[1]]、[[2]]。"
            "涉及金额、日期、天数、比例、职级或审批人时必须逐字核对原文。资料不足或相互冲突时明确说明无法确认。"
            "禁止复述文档标题、章节目录、原始 Markdown 表格或分隔线，禁止把检索资料原样抄写成答案。"
            f"{requirement_text}使用简洁中文和 Markdown，不要输出参考资料列表之外的链接。"
        )
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

        async def ollama_deltas() -> AsyncIterator[str]:
            emitted = False
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
                                    emitted = True
                                    yield delta
                    break
                except (httpx.HTTPError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    if emitted:
                        raise
                    async for delta in _chunked_text(fallback.content):
                        yield delta
                    break

        async def openai_deltas() -> AsyncIterator[str]:
            emitted = False
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
                                emitted = True
                                yield delta
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
                if emitted:
                    raise
                async for delta in _chunked_text(fallback.content):
                    yield delta

        async def validated_deltas(source: AsyncIterator[str]) -> AsyncIterator[str]:
            buffer = ""
            released = False
            valid_markers = {f"[[{index}]]" for index in range(1, len(fallback.citations) + 1)}
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
                if context_dump or len(buffer) > 240:
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
