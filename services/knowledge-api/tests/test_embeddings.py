from __future__ import annotations

import json

import httpx

from knowledge_api.embeddings import EmbeddingManager, HASHING_MODEL_ID


def test_ollama_embedding_provider_uses_local_batch_api() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        inputs = payload["input"]
        return httpx.Response(200, json={"embeddings": [[1.0, float(index), 0.5] for index, _ in enumerate(inputs)]})

    manager = EmbeddingManager(
        "ollama",
        base_url="http://127.0.0.1:11434",
        model="bge-m3",
        transport=httpx.MockTransport(handler),
    )
    vectors = manager.embed(["第一段", "第二段"])

    assert manager.model_id == "ollama:bge-m3"
    assert manager.dimensions == 3
    assert manager.status().state == "ready"
    assert len(vectors) == 2
    assert requests[-1]["model"] == "bge-m3"


def test_ollama_embedding_provider_reuses_exact_local_vectors() -> None:
    requests: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        requests.append(inputs)
        return httpx.Response(200, json={"embeddings": [[float(len(text)), 1.0] for text in inputs]})

    manager = EmbeddingManager(
        "ollama",
        base_url="http://127.0.0.1:11434",
        model="bge-m3",
        transport=httpx.MockTransport(handler),
    )
    first = manager.embed(["重复问题", "另一问题"])
    repeated = manager.embed(["另一问题", "重复问题", "重复问题"])

    assert repeated == [first[1], first[0], first[0]]
    assert len(requests) == 2
    assert requests[-1] == ["重复问题", "另一问题"]


def test_auto_embedding_falls_back_without_local_model() -> None:
    manager = EmbeddingManager(
        "auto",
        transport=httpx.MockTransport(lambda _: httpx.Response(503)),
    )

    assert manager.model_id == HASHING_MODEL_ID
    assert manager.status().state == "fallback"
    assert len(manager.embed(["本地回退"])[0]) == 256


def test_ollama_embedding_retries_on_cpu_for_incompatible_cuda() -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if payload.get("options") != {"num_gpu": 0}:
            return httpx.Response(500, json={"error": "CUDA error: PTX was compiled with an unsupported toolchain"})
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})

    manager = EmbeddingManager(
        "ollama",
        base_url="http://127.0.0.1:11434",
        model="bge-m3",
        transport=httpx.MockTransport(handler),
    )

    assert manager.dimensions == 3
    assert manager.status().execution_mode == "cpu-compatibility"
    assert calls[1]["options"] == {"num_gpu": 0}
