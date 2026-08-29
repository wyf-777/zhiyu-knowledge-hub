from __future__ import annotations

import json

import httpx
import pytest

from knowledge_api.modeling import ModelManager


@pytest.mark.anyio
async def test_ollama_chat_retries_on_cpu_for_incompatible_cuda(tmp_path) -> None:
    calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        payload = json.loads(request.content)
        calls.append(payload)
        if payload.get("options", {}).get("num_gpu") != 0:
            return httpx.Response(500, json={"error": "CUDA error: PTX was compiled with an unsupported toolchain"})
        return httpx.Response(200, json={"message": {"content": "依据本地制度回答。 [[1]]"}})

    manager = ModelManager(tmp_path, transport=httpx.MockTransport(handler))
    answer = await manager.build(
        "制度是什么？",
        [{
            "chunk_id": "chunk-1",
            "document_id": "document-1",
            "document_name": "制度.txt",
            "format": "TXT",
            "page": 1,
            "heading": "第一章",
            "content": "依据本地制度回答。",
            "score": 0.9,
            "evidence_score": 0.9,
            "updated_at": "2026-08-29T00:00:00Z",
        }],
    )

    assert answer.provider == "ollama:qwen3:8b"
    assert "num_gpu" not in calls[0]["options"]
    assert calls[1]["options"]["num_gpu"] == 0
    status = await manager.status(force=True)
    assert status.execution_mode == "cpu-compatibility"
    assert "CPU" in status.message


@pytest.mark.anyio
async def test_follow_up_query_is_rewritten_with_local_conversation_context(tmp_path) -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        payload = json.loads(request.content)
        payloads.append(payload)
        return httpx.Response(200, json={"message": {"content": "上海部门负责人出差时的住宿上限是多少？"}})

    manager = ModelManager(tmp_path, transport=httpx.MockTransport(handler))
    rewritten = await manager.rewrite_query(
        "继续比较上述标准在部门负责人审批责任和例外条件方面的差异。",
        [
            {"role": "user", "content": "上海普通员工的住宿上限是多少？"},
            {"role": "assistant", "content": "普通员工每晚 500 元。"},
        ],
    )

    assert rewritten == "上海部门负责人出差时的住宿上限是多少？"
    assert payloads[0]["stream"] is False
    assert payloads[0]["think"] is False
    assert payloads[0]["keep_alive"] == "15m"


@pytest.mark.anyio
async def test_short_follow_up_uses_fast_deterministic_retrieval_query(tmp_path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("短追问不应额外调用大模型")

    manager = ModelManager(tmp_path, transport=httpx.MockTransport(handler))
    rewritten = await manager.rewrite_query(
        "那部门负责人呢？",
        [{"role": "user", "content": "上海普通员工的住宿上限是多少？"}],
    )

    assert rewritten == "上海普通员工的住宿上限是多少？\n追问：那部门负责人呢？"


@pytest.mark.anyio
async def test_ollama_answer_is_forwarded_as_native_stream(tmp_path) -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        payload = json.loads(request.content)
        payloads.append(payload)
        body = (
            json.dumps({"message": {"content": "负责人上限"}}, ensure_ascii=False) + "\n"
            + json.dumps({"message": {"content": "为 650 元。 [[1]]"}, "done": True}, ensure_ascii=False) + "\n"
        )
        return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "application/x-ndjson"})

    manager = ModelManager(tmp_path, transport=httpx.MockTransport(handler))
    answer_stream = await manager.stream(
        "负责人上限是多少？",
        [{
            "chunk_id": "chunk-1",
            "document_id": "document-1",
            "document_name": "制度.txt",
            "format": "TXT",
            "page": 1,
            "heading": "差旅标准",
            "content": "部门负责人住宿上限为每晚 650 元。",
            "parent_content": "普通员工每晚 500 元，部门负责人每晚 650 元。",
            "score": 0.9,
            "evidence_score": 0.9,
            "updated_at": "2026-08-29T00:00:00Z",
        }],
    )
    deltas = [delta async for delta in answer_stream.deltas]

    assert "".join(deltas) == "负责人上限为 650 元。 [[1]]"
    assert payloads[0]["stream"] is True
    assert payloads[0]["keep_alive"] == "15m"
    assert payloads[0]["options"]["num_predict"] == 180
    assert payloads[0]["options"]["num_ctx"] == 4096


@pytest.mark.anyio
async def test_stream_rejects_raw_context_dump_before_showing_it(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        body = json.dumps({"message": {"content": "### 制度原文\n---\n|地点|时间|\n|---|---|"}}, ensure_ascii=False) + "\n"
        return httpx.Response(200, content=body.encode("utf-8"))

    manager = ModelManager(tmp_path, transport=httpx.MockTransport(handler))
    answer_stream = await manager.stream(
        "总部上班时间？",
        [{
            "chunk_id": "chunk-1", "document_id": "document-1", "document_name": "考勤制度.txt",
            "format": "TXT", "page": 1, "heading": "工作时间", "content": "总部工作时间为9:00至18:00。",
            "parent_content": "# 考勤制度\n总部工作时间为9:00至18:00。", "score": 0.9,
            "evidence_score": 0.9, "updated_at": "2026-08-29T00:00:00Z",
        }],
    )
    content = "".join([delta async for delta in answer_stream.deltas])

    assert not content.lstrip().startswith("#")
    assert "制度原文" not in content
    assert "9:00" in content
    assert "[[1]]" in content


@pytest.mark.anyio
async def test_complete_list_question_forbids_omitting_items(tmp_path) -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        payload = json.loads(request.content)
        payloads.append(payload)
        body = json.dumps({"message": {"content": "甲、乙、丙。 [[1]]"}, "done": True}, ensure_ascii=False) + "\n"
        return httpx.Response(200, content=body.encode("utf-8"))

    manager = ModelManager(tmp_path, transport=httpx.MockTransport(handler))
    answer = await manager.stream(
        "请将所有项目完整罗列出来",
        [{
            "chunk_id": "chunk-1", "document_id": "document-1", "document_name": "名单.txt",
            "format": "TXT", "page": 1, "heading": "名单", "content": "甲、乙、丙。",
            "score": 0.9, "evidence_score": 0.9, "updated_at": "2026-08-29T00:00:00Z",
        }],
    )
    _ = [delta async for delta in answer.deltas]

    system_prompt = payloads[0]["messages"][0]["content"]
    assert "禁止用‘等’‘等等’省略" in system_prompt
