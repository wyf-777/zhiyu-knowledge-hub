from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass
from typing import Literal, Protocol
from urllib.parse import urlparse

import httpx

EmbeddingProviderName = Literal["auto", "hashing", "ollama"]
HASHING_MODEL_ID = "hashing-vector-v1"
HASHING_DIMENSIONS = 256


def text_features(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    features = re.findall(r"[a-z0-9][a-z0-9_.-]*", normalized)
    for run in re.findall(r"[\u3400-\u9fff]+", normalized):
        features.extend(run[index:index + 2] for index in range(max(1, len(run) - 1)))
        if len(run) >= 3:
            features.extend(run[index:index + 3] for index in range(len(run) - 2))
    return features


def hashing_embed(text: str) -> list[float]:
    vector = [0.0] * HASHING_DIMENSIONS
    for feature in text_features(text):
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % HASHING_DIMENSIONS
        vector[index] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in vector))
    return [round(value / norm, 6) for value in vector] if norm else vector


def normalize_loopback_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Embedding 服务地址必须是本机 HTTP 回环地址。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.port:
        raise ValueError("Embedding 服务地址必须包含端口，且不能包含凭据或查询参数。")
    return normalized


@dataclass(frozen=True)
class EmbeddingStatus:
    provider: str
    state: Literal["ready", "fallback", "error"]
    model_id: str
    dimensions: int
    index_version: int
    execution_mode: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class EmbeddingProvider(Protocol):
    model_id: str
    dimensions: int
    index_version: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbeddingProvider:
    model_id = HASHING_MODEL_ID
    dimensions = HASHING_DIMENSIONS
    index_version = 2

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hashing_embed(text) for text in texts]


class OllamaEmbeddingProvider:
    index_version = 2

    def __init__(self, base_url: str, model: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = normalize_loopback_url(base_url)
        self.model = model.strip()
        if not self.model:
            raise ValueError("Embedding 模型名称不能为空。")
        self.model_id = f"ollama:{self.model}"
        self._transport = transport
        self._force_cpu = False
        probe = self.embed(["知域本地知识库索引"])
        if not probe or not probe[0]:
            raise ValueError("Embedding 模型没有返回有效向量。")
        self.dimensions = len(probe[0])

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, object] = {"model": self.model, "input": texts}
        if self._force_cpu:
            payload["options"] = {"num_gpu": 0}
        with httpx.Client(timeout=90, transport=self._transport, trust_env=False) as client:
            response = client.post(f"{self.base_url}/api/embed", json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                error_text = response.text.lower()
                cuda_incompatible = response.status_code == 500 and ("cuda error" in error_text or "unsupported toolchain" in error_text)
                if self._force_cpu or not cuda_incompatible:
                    raise
                self._force_cpu = True
                response = client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": texts, "options": {"num_gpu": 0}},
                )
                response.raise_for_status()
            embeddings = response.json().get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ValueError("Ollama Embedding 返回数量与输入不一致。")
        vectors = [[float(value) for value in vector] for vector in embeddings]
        if not vectors or any(not vector or len(vector) != len(vectors[0]) for vector in vectors):
            raise ValueError("Ollama Embedding 返回了无效向量。")
        return vectors

    @property
    def execution_mode(self) -> str:
        return "cpu-compatibility" if self._force_cpu else "auto"


class EmbeddingManager:
    def __init__(
        self,
        provider: EmbeddingProviderName = "hashing",
        *,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "bge-m3",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.requested_provider = provider
        self._fallback_reason = ""
        if provider not in {"auto", "hashing", "ollama"}:
            raise ValueError("不支持的 Embedding 提供器。")
        if provider == "hashing":
            self.provider: EmbeddingProvider = HashingEmbeddingProvider()
            return
        try:
            self.provider = OllamaEmbeddingProvider(base_url, model, transport=transport)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            if provider == "ollama":
                raise
            self.provider = HashingEmbeddingProvider()
            self._fallback_reason = str(error)

    @property
    def model_id(self) -> str:
        return self.provider.model_id

    @property
    def dimensions(self) -> int:
        return self.provider.dimensions

    @property
    def index_version(self) -> int:
        return self.provider.index_version

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.provider.embed(texts)

    def status(self) -> EmbeddingStatus:
        if isinstance(self.provider, OllamaEmbeddingProvider):
            message = "正在使用本机 Ollama Embedding。"
            if self.provider.execution_mode == "cpu-compatibility":
                message = "Ollama GPU 加载不兼容，当前使用 CPU 兼容模式生成向量。"
            return EmbeddingStatus("ollama", "ready", self.model_id, self.dimensions, self.index_version, self.provider.execution_mode, message)
        if self.requested_provider == "auto" and self._fallback_reason:
            return EmbeddingStatus("hashing", "fallback", self.model_id, self.dimensions, self.index_version, "local", "未检测到可用的本地 Embedding 模型，当前使用轻量回退索引。")
        return EmbeddingStatus("hashing", "ready", self.model_id, self.dimensions, self.index_version, "local", "当前使用轻量本地索引。")
