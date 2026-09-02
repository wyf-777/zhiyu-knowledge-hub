from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import gettempdir
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
import httpx
from starlette.concurrency import run_in_threadpool

from . import __version__
from .adaptive_retrieval import AdaptiveRetriever
from .data_reliability import BackupManager, BackupValidationError
from .embeddings import EmbeddingManager, EmbeddingProviderName
from .ingestion import DocumentProcessor
from .local_files import open_local_document
from .modeling import ModelManager, ModelProvider, ModelSettings, query_context_needs_similarity
from .reranking import RerankerManager, RerankerProviderName
from .storage import KnowledgeStore

PROTOCOL_VERSION = "1"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_BACKUP_UPLOAD_BYTES = 20 * 1024 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".pdf": "pdf", ".docx": "docx", ".xls": "xls", ".xlsx": "xlsx", ".pptx": "pptx",
    ".html": "html", ".htm": "html", ".txt": "txt", ".md": "md", ".markdown": "md",
}


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    knowledge_base_ids: list[str] = Field(min_length=1, max_length=20)
    limit: int = Field(default=8, ge=1, le=30)
    document_ids: list[str] = Field(default_factory=list, max_length=50)


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatCompletionRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    knowledge_base_ids: list[str] = Field(min_length=1, max_length=20)
    conversation_id: str | None = Field(default=None, max_length=160)
    stream: bool = True
    document_ids: list[str] = Field(default_factory=list, max_length=50)
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=12)


class ModelSettingsRequest(BaseModel):
    provider: ModelProvider
    base_url: str = Field(min_length=1, max_length=300)
    model: str = Field(min_length=1, max_length=160)
    timeout_seconds: int = Field(default=90, ge=10, le=300)


class ConversationCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    knowledge_base_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=300)
    pinned: bool = False


class ConversationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    pinned: bool | None = None


class PersistedChatMessage(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=100_000)
    time: str = Field(default="", max_length=40)
    citations: list[dict[str, Any]] | None = Field(default=None, max_length=100)
    suggestions: list[str] | None = Field(default=None, max_length=30)
    status: Literal["complete", "streaming", "stopped", "no-evidence", "error"] | None = None
    feedback: Literal["helpful", "unhelpful"] | None = None


class ConversationMessagesRequest(BaseModel):
    items: list[PersistedChatMessage] = Field(default_factory=list, max_length=5000)


class LegacyConversation(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    knowledge_base_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=300)
    pinned: bool = False


class LegacyConversationImportRequest(BaseModel):
    import_id: str = Field(min_length=1, max_length=200)
    conversations: list[LegacyConversation] = Field(default_factory=list, max_length=5000)
    messages: dict[str, list[PersistedChatMessage]] = Field(default_factory=dict)


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def create_app(
    runtime_token: str,
    shutdown_callback: Callable[[], None] | None = None,
    data_dir: Path | None = None,
    *,
    default_model_provider: ModelProvider = "auto",
    model_transport: httpx.AsyncBaseTransport | None = None,
    default_embedding_provider: EmbeddingProviderName = "hashing",
    embedding_base_url: str = "http://127.0.0.1:11434",
    embedding_model: str = "bge-m3",
    embedding_transport: httpx.BaseTransport | None = None,
    default_reranker_provider: RerankerProviderName = "feature",
    document_opener: Callable[[Path], None] = open_local_document,
) -> FastAPI:
    if not runtime_token:
        raise ValueError("runtime_token must not be empty")

    store = KnowledgeStore(data_dir or Path(gettempdir()) / "zhiyu-knowledge-api")
    backup_manager = BackupManager(store)
    embedding_manager = EmbeddingManager(
        default_embedding_provider,
        base_url=embedding_base_url,
        model=embedding_model,
        transport=embedding_transport,
    )
    reranker_manager = RerankerManager(default_reranker_provider)
    processor = DocumentProcessor(store, embedding_manager, reranker_manager)
    model_manager = ModelManager(store.data_dir, default_provider=default_model_provider, transport=model_transport)
    adaptive_retriever = AdaptiveRetriever(processor, embedding_manager, model_manager)
    app = FastAPI(title="Zhiyu Knowledge API", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime_token = runtime_token
    app.state.store = store
    app.state.backup_manager = backup_manager
    app.state.processor = processor
    app.state.embedding_manager = embedding_manager
    app.state.reranker_manager = reranker_manager
    app.state.model_manager = model_manager
    app.state.adaptive_retriever = adaptive_retriever
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "http://127.0.0.1:4173",
            "http://localhost:4173",
        ],
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def authenticate_local_request(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        if request.method == "OPTIONS":
            return await call_next(request)
        expected = f"Bearer {app.state.runtime_token}"
        if not secrets.compare_digest(request.headers.get("authorization", ""), expected):
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "LOCAL_AUTH_REQUIRED", "message": "本地服务认证失败。", "request_id": None}},
            )
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def handle_api_error(_: Request, error: HTTPException) -> JSONResponse:
        if isinstance(error.detail, dict) and "code" in error.detail:
            return JSONResponse(status_code=error.status_code, content={"error": {**error.detail, "request_id": None}})
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": "REQUEST_FAILED", "message": str(error.detail), "request_id": None}},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "knowledge-api", "version": __version__, "protocol_version": PROTOCOL_VERSION}

    @app.get("/api/v1/runtime/status")
    async def runtime_status() -> dict[str, object]:
        model_status = await model_manager.status()
        return {
            "status": "running",
            "service": "knowledge-api",
            "version": __version__,
            "protocol_version": PROTOCOL_VERSION,
            "storage_mode": "local",
            "index_mode": "hybrid-rrf-rerank-v1",
            "embedding": embedding_manager.status().to_dict(),
            "reranker": reranker_manager.status().to_dict(),
            "ocr": processor.ocr.status().to_dict(),
            "indexes": store.index_summary(),
            "model": asdict(model_status),
        }

    @app.get("/api/v1/model/settings")
    async def get_model_settings() -> dict[str, object]:
        return {"settings": asdict(model_manager.get_settings()), "status": asdict(await model_manager.status())}

    @app.put("/api/v1/model/settings")
    async def update_model_settings(payload: ModelSettingsRequest) -> dict[str, object]:
        try:
            settings = model_manager.update_settings(ModelSettings(**payload.model_dump()))
        except ValueError as error:
            raise api_error(400, "MODEL_SETTINGS_INVALID", str(error)) from error
        return {"settings": asdict(settings), "status": asdict(await model_manager.status(force=True))}

    @app.post("/api/v1/model/detect")
    async def detect_model() -> dict[str, object]:
        return {"status": asdict(await model_manager.status(force=True))}

    @app.get("/api/v1/knowledge-bases")
    async def list_knowledge_bases() -> dict[str, object]:
        return {"items": store.list_knowledge_bases()}

    @app.get("/api/v1/conversations")
    async def list_conversations(knowledge_base_id: str | None = None) -> dict[str, object]:
        if knowledge_base_id and not store.get_knowledge_base(knowledge_base_id):
            raise api_error(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在。")
        return {"items": store.list_conversations(knowledge_base_id)}

    @app.post("/api/v1/conversations", status_code=201)
    async def create_conversation(payload: ConversationCreateRequest) -> dict[str, object]:
        if not store.get_knowledge_base(payload.knowledge_base_id):
            raise api_error(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在。")
        return {
            "conversation": store.create_conversation(
                conversation_id=payload.id,
                knowledge_base_id=payload.knowledge_base_id,
                title=payload.title,
                pinned=payload.pinned,
            )
        }

    @app.put("/api/v1/conversations/{conversation_id}")
    async def update_conversation(conversation_id: str, payload: ConversationUpdateRequest) -> dict[str, object]:
        conversation = store.update_conversation(conversation_id, title=payload.title, pinned=payload.pinned)
        if not conversation:
            raise api_error(404, "CONVERSATION_NOT_FOUND", "对话不存在。")
        return {"conversation": conversation}

    @app.delete("/api/v1/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: str) -> Response:
        if not store.delete_conversation(conversation_id):
            raise api_error(404, "CONVERSATION_NOT_FOUND", "对话不存在。")
        return Response(status_code=204)

    @app.get("/api/v1/conversations/{conversation_id}/messages")
    async def list_conversation_messages(conversation_id: str) -> dict[str, object]:
        if not store.get_conversation(conversation_id):
            raise api_error(404, "CONVERSATION_NOT_FOUND", "对话不存在。")
        return {"items": store.list_conversation_messages(conversation_id)}

    @app.put("/api/v1/conversations/{conversation_id}/messages")
    async def replace_conversation_messages(conversation_id: str, payload: ConversationMessagesRequest) -> dict[str, object]:
        try:
            items = store.replace_conversation_messages(conversation_id, [item.model_dump() for item in payload.items])
        except ValueError:
            raise api_error(404, "CONVERSATION_NOT_FOUND", "对话不存在。") from None
        return {"items": items}

    @app.post("/api/v1/conversations/import", status_code=200)
    async def import_legacy_conversations(payload: LegacyConversationImportRequest) -> dict[str, object]:
        try:
            result = store.import_legacy_conversations(
                import_id=payload.import_id,
                conversations=[
                    {
                        "id": item.id,
                        "knowledge_base_id": item.knowledge_base_id,
                        "title": item.title,
                        "pinned": item.pinned,
                    }
                    for item in payload.conversations
                ],
                messages_by_conversation={
                    conversation_id: [message.model_dump() for message in messages]
                    for conversation_id, messages in payload.messages.items()
                },
            )
        except ValueError as error:
            raise api_error(400, "LEGACY_IMPORT_INVALID", str(error)) from None
        return result

    @app.get("/api/v1/storage/status")
    async def storage_status() -> dict[str, object]:
        return {
            "schema": store.schema_status(),
            "data_directory": str(store.data_dir),
            "backup_directory": str(store.backups_dir),
            "conversation_count": len(store.list_conversations()),
        }

    @app.get("/api/v1/storage/backups")
    async def list_backups() -> dict[str, object]:
        return {"items": await run_in_threadpool(backup_manager.list_backups)}

    @app.post("/api/v1/storage/backups", status_code=201)
    async def create_backup() -> dict[str, object]:
        try:
            backup = await run_in_threadpool(backup_manager.create_backup)
        except RuntimeError as error:
            raise api_error(409, "BACKUP_BUSY", str(error)) from None
        except (BackupValidationError, OSError) as error:
            raise api_error(500, "BACKUP_FAILED", str(error)) from None
        return {"backup": backup}

    @app.post("/api/v1/storage/backups/import", status_code=201)
    async def import_backup(backup: Annotated[UploadFile, File(description="知域本地备份")]) -> dict[str, object]:
        temporary = store.data_dir / f".backup-upload-{uuid4().hex}.tmp"
        total = 0
        try:
            with temporary.open("wb") as output:
                while chunk := await backup.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_BACKUP_UPLOAD_BYTES:
                        raise api_error(413, "BACKUP_TOO_LARGE", "备份文件超过20GB安全限制。")
                    output.write(chunk)
            item = await run_in_threadpool(backup_manager.import_backup, temporary)
            return {"backup": item}
        except BackupValidationError as error:
            raise api_error(400, "BACKUP_INVALID", str(error)) from None
        finally:
            await backup.close()
            temporary.unlink(missing_ok=True)

    @app.post("/api/v1/storage/backups/{backup_id}/restore")
    async def restore_backup(backup_id: str) -> dict[str, object]:
        try:
            return await run_in_threadpool(backup_manager.restore_backup, backup_id)
        except FileNotFoundError:
            raise api_error(404, "BACKUP_NOT_FOUND", "备份不存在。") from None
        except RuntimeError as error:
            raise api_error(409, "RESTORE_BUSY", str(error)) from None
        except BackupValidationError as error:
            raise api_error(400, "BACKUP_INVALID", str(error)) from None
        except OSError as error:
            raise api_error(500, "RESTORE_FAILED", f"恢复失败，原数据已保留：{error}") from None

    @app.post("/api/v1/storage/backups/open-folder")
    async def open_backup_folder() -> dict[str, str]:
        try:
            document_opener(store.backups_dir)
        except OSError as error:
            raise api_error(500, "BACKUP_FOLDER_OPEN_FAILED", f"无法打开备份目录：{error}") from None
        return {"status": "opened"}

    @app.get("/api/v1/knowledge-bases/{knowledge_base_id}/documents")
    async def list_documents(knowledge_base_id: str) -> dict[str, object]:
        if not store.get_knowledge_base(knowledge_base_id):
            raise api_error(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在。")
        return {"items": store.list_documents(knowledge_base_id)}

    @app.post("/api/v1/knowledge-bases/{knowledge_base_id}/documents", status_code=202)
    async def upload_documents(
        knowledge_base_id: str,
        background_tasks: BackgroundTasks,
        files: Annotated[list[UploadFile], File(description="待导入的本地文档")],
    ) -> dict[str, object]:
        if not store.get_knowledge_base(knowledge_base_id):
            raise api_error(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在。")
        if not files:
            raise api_error(400, "UPLOAD_EMPTY", "请选择至少一个文档。")

        unsupported = [
            Path(upload.filename or "unnamed").name
            for upload in files
            if Path(Path(upload.filename or "unnamed").name).suffix.lower() not in ALLOWED_EXTENSIONS
        ]
        if unsupported:
            display_names = "、".join(unsupported[:5])
            suffix = " 等文件" if len(unsupported) > 5 else ""
            raise api_error(415, "DOCUMENT_TYPE_UNSUPPORTED", f"以下文件格式暂不支持：{display_names}{suffix}。本批次未导入任何文件。")

        accepted = []
        for upload in files:
            source_name = Path(upload.filename or "unnamed").name
            suffix = Path(source_name).suffix.lower()
            source_type = ALLOWED_EXTENSIONS.get(suffix)
            if not source_type:
                raise api_error(415, "DOCUMENT_TYPE_UNSUPPORTED", f"暂不支持 {suffix or '未知'} 格式。")
            content_type = upload.content_type or "application/octet-stream"
            content = await upload.read(MAX_UPLOAD_BYTES + 1)
            await upload.close()
            if len(content) > MAX_UPLOAD_BYTES:
                raise api_error(413, "DOCUMENT_TOO_LARGE", "单个文档不能超过 50 MB。")
            if not content:
                raise api_error(400, "DOCUMENT_EMPTY", f"{source_name} 是空文件。")

            storage_id = str(uuid4())
            relative_path = Path("documents") / knowledge_base_id / f"{storage_id}{suffix}"
            absolute_path = (store.data_dir / relative_path).resolve()
            absolute_path.parent.mkdir(parents=True, exist_ok=True)
            absolute_path.write_bytes(content)
            document = store.create_document(
                knowledge_base_id=knowledge_base_id,
                source_name=source_name,
                source_type=source_type,
                object_path=relative_path.as_posix(),
                content_hash=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                mime_type=content_type,
            )
            job = store.create_job(document["id"])
            background_tasks.add_task(processor.process, document["id"], job["id"])
            accepted.append({"document": document, "job": job})
        return {"items": accepted}

    @app.delete("/api/v1/documents/{document_id}", status_code=204)
    async def delete_document(document_id: str) -> Response:
        relative_path = store.delete_document(document_id)
        if relative_path is None:
            raise api_error(404, "DOCUMENT_NOT_FOUND", "文档不存在。")
        absolute_path = (store.data_dir / relative_path).resolve()
        if absolute_path.is_relative_to(store.documents_dir):
            absolute_path.unlink(missing_ok=True)
        return Response(status_code=204)

    @app.post("/api/v1/documents/{document_id}/open")
    async def open_document(document_id: str) -> dict[str, str]:
        document = store.get_document(document_id)
        if not document:
            raise api_error(404, "DOCUMENT_NOT_FOUND", "文档不存在。")
        absolute_path = (store.data_dir / str(document["object_path"])).resolve()
        if not absolute_path.is_relative_to(store.documents_dir) or not absolute_path.is_file():
            raise api_error(404, "DOCUMENT_FILE_NOT_FOUND", "本地原文文件不存在。")
        try:
            await run_in_threadpool(document_opener, absolute_path)
        except OSError as error:
            raise api_error(500, "DOCUMENT_OPEN_FAILED", "无法使用系统默认应用打开原文。") from error
        return {"status": "opened"}

    @app.get("/api/v1/documents/{document_id}/chunks/{chunk_id}/preview")
    async def preview_document_location(document_id: str, chunk_id: str) -> Response:
        document = store.get_document(document_id)
        chunk = store.get_chunk(chunk_id)
        if not document or not chunk or str(chunk["document_id"]) != document_id:
            raise api_error(404, "DOCUMENT_LOCATION_NOT_FOUND", "引用位置不存在或已失效。")
        if str(document["source_type"]) != "pdf":
            raise api_error(415, "DOCUMENT_PREVIEW_UNSUPPORTED", "当前只有 PDF 支持坐标高亮预览。")
        location = json.loads(str(chunk.get("location_json") or "null"))
        if not isinstance(location, dict) or not location.get("page"):
            raise api_error(404, "DOCUMENT_LOCATION_NOT_FOUND", "该引用没有可用的 PDF 坐标。")
        absolute_path = (store.data_dir / str(document["object_path"])).resolve()
        if not absolute_path.is_relative_to(store.documents_dir) or not absolute_path.is_file():
            raise api_error(404, "DOCUMENT_FILE_NOT_FOUND", "本地原文文件不存在。")

        def render_preview() -> bytes:
            import pymupdf

            with pymupdf.open(absolute_path) as pdf:
                page_number = int(location["page"])
                if page_number < 1 or page_number > len(pdf):
                    raise ValueError("PDF 页码超出范围")
                page = pdf.load_page(page_number - 1)
                bbox = location.get("bbox")
                if isinstance(bbox, list) and len(bbox) == 4:
                    rectangle = pymupdf.Rect(*(float(value) for value in bbox))
                    page.draw_rect(rectangle, color=(0.25, 0.25, 0.25), fill=(0.75, 0.75, 0.75), fill_opacity=0.28, width=1.2, overlay=True)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6), colorspace=pymupdf.csRGB, alpha=False)
                return pixmap.tobytes("png")

        try:
            preview = await run_in_threadpool(render_preview)
        except (ValueError, RuntimeError) as error:
            raise api_error(422, "DOCUMENT_PREVIEW_FAILED", f"无法生成 PDF 定位预览：{error}") from error
        return Response(content=preview, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/documents/{document_id}/retry", status_code=202)
    async def retry_document(document_id: str, background_tasks: BackgroundTasks) -> dict[str, object]:
        document = store.get_document(document_id)
        if not document:
            raise api_error(404, "DOCUMENT_NOT_FOUND", "文档不存在。")
        store.update_document(document_id, status="queued", error_code=None, error_message=None)
        job = store.create_job(document_id, store.latest_attempt(document_id) + 1)
        background_tasks.add_task(processor.process, document_id, job["id"])
        return {"job": job}

    @app.post("/api/v1/knowledge-bases/{knowledge_base_id}/reindex", status_code=202)
    async def reindex_knowledge_base(knowledge_base_id: str, background_tasks: BackgroundTasks) -> dict[str, object]:
        if not store.get_knowledge_base(knowledge_base_id):
            raise api_error(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在。")
        jobs: list[dict[str, object]] = []
        for document in store.list_documents(knowledge_base_id):
            store.update_document(str(document["id"]), status="queued", error_code=None, error_message=None)
            job = store.create_job(str(document["id"]), store.latest_attempt(str(document["id"])) + 1)
            background_tasks.add_task(processor.process, str(document["id"]), str(job["id"]))
            jobs.append(job)
        return {"items": jobs, "embedding": embedding_manager.status().to_dict()}

    @app.get("/api/v1/jobs")
    async def list_jobs(limit: int = 50) -> dict[str, object]:
        return {"items": store.list_jobs(max(1, min(limit, 100)))}

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, object]:
        job = store.get_job(job_id)
        if not job:
            raise api_error(404, "JOB_NOT_FOUND", "后台任务不存在。")
        return {"job": job}

    @app.post("/api/v1/retrieval/search")
    async def search(request: SearchRequest) -> dict[str, object]:
        if any(not store.get_knowledge_base(item) for item in request.knowledge_base_ids):
            raise api_error(404, "KNOWLEDGE_BASE_NOT_FOUND", "检索范围包含不存在的知识库。")
        if any((document := store.get_document(item)) is None or document["knowledge_base_id"] not in request.knowledge_base_ids for item in request.document_ids):
            raise api_error(400, "DOCUMENT_SCOPE_INVALID", "指定文档不属于当前检索范围。")
        return {
            "items": processor.search(request.query.strip(), request.knowledge_base_ids, request.limit, request.document_ids),
            "index_mode": "hybrid-rrf-rerank-v1",
            "embedding": embedding_manager.status().to_dict(),
            "reranker": reranker_manager.status().to_dict(),
        }

    @app.post("/api/v1/chat/completions")
    async def chat_completion(payload: ChatCompletionRequest, request: Request) -> StreamingResponse:
        request_started_at = time.perf_counter()
        if any(not store.get_knowledge_base(item) for item in payload.knowledge_base_ids):
            raise api_error(404, "KNOWLEDGE_BASE_NOT_FOUND", "问答范围包含不存在的知识库。")
        if any((document := store.get_document(item)) is None or document["knowledge_base_id"] not in payload.knowledge_base_ids for item in payload.document_ids):
            raise api_error(400, "DOCUMENT_SCOPE_INVALID", "指定文档不属于当前问答范围。")
        history = [item.model_dump() for item in payload.history[-10:]]
        current_question = payload.message.strip()
        topic_similarity: float | None = None
        if query_context_needs_similarity(current_question, history):
            previous_question = next(
                (
                    str(item.get("content") or "").strip()
                    for item in reversed(history)
                    if item.get("role") == "user" and item.get("content")
                ),
                "",
            )
            if previous_question:
                try:
                    topic_similarity = await run_in_threadpool(
                        embedding_manager.similarity,
                        current_question,
                        previous_question,
                    )
                except (httpx.HTTPError, ValueError, TypeError):
                    topic_similarity = None
        query_plan = await model_manager.plan_query(
            current_question,
            history,
            topic_similarity=topic_similarity,
        )
        planning_completed_at = time.perf_counter()
        retrieval_outcome = await adaptive_retriever.retrieve(
            query_plan,
            payload.knowledge_base_ids,
            limit=12,
            document_ids=payload.document_ids,
        )
        retrieval_queries = list(retrieval_outcome.queries)
        search_results = retrieval_outcome.results
        retrieval_completed_at = time.perf_counter()
        # Use the standalone question for both retrieval and answer grounding.
        # Otherwise a follow-up such as “是哪16座” retrieves the right table but
        # the compressor cannot know which list inside that table must be kept.
        answer_history = history if query_plan.use_answer_history else []
        answer = await model_manager.stream(query_plan.standalone_question, search_results, answer_history)
        answer_prepared_at = time.perf_counter()
        embedding_status = embedding_manager.status()
        message_id = str(uuid4())

        def elapsed_ms(started_at: float, completed_at: float | None = None) -> int:
            return round(((completed_at or time.perf_counter()) - started_at) * 1000)

        def event(name: str, **data: object) -> bytes:
            return (json.dumps({"event": name, **data}, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

        async def stream_events():
            yield event(
                "message.start",
                message_id=message_id,
                provider=answer.provider,
                timings_ms={
                    "planning": elapsed_ms(request_started_at, planning_completed_at),
                    "retrieval": elapsed_ms(planning_completed_at, retrieval_completed_at),
                    "answer_setup": elapsed_ms(retrieval_completed_at, answer_prepared_at),
                    "before_stream": elapsed_ms(request_started_at, answer_prepared_at),
                },
                diagnostics={
                    "query_context": query_plan.decision,
                    "query_rewritten": query_plan.standalone_question != current_question,
                    "retrieval_query_count": len(retrieval_queries),
                    "retrieval_candidate_count": len(search_results),
                    "embedding_execution_mode": embedding_status.execution_mode,
                    "embedding_model": embedding_status.model_id,
                    **retrieval_outcome.diagnostics(),
                },
            )
            yield event(
                "retrieval.completed",
                count=len(answer.citations),
                no_evidence=answer.no_evidence,
                latency_ms=elapsed_ms(planning_completed_at, retrieval_completed_at),
            )
            for citation in answer.citations:
                yield event("citation", citation=citation)
            first_delta_at: float | None = None
            visible_character_count = 0
            async for delta in answer.deltas:
                if await request.is_disconnected():
                    return
                if first_delta_at is None:
                    first_delta_at = time.perf_counter()
                visible_character_count += len(delta)
                yield event("message.delta", delta=delta)
            completed_at = time.perf_counter()
            yield event(
                "message.completed",
                message_id=message_id,
                finish_reason="stop",
                no_evidence=answer.no_evidence,
                suggestions=answer.suggestions,
                provider=answer.provider,
                timings_ms={
                    "first_answer_delta": elapsed_ms(request_started_at, first_delta_at) if first_delta_at else None,
                    "generation": elapsed_ms(answer_prepared_at, completed_at),
                    "total": elapsed_ms(request_started_at, completed_at),
                },
                diagnostics={
                    **answer.diagnostics,
                    "citation_count": len(answer.citations),
                    "visible_character_count": visible_character_count,
                },
            )

        return StreamingResponse(
            stream_events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/api/v1/runtime/shutdown", status_code=202)
    async def shutdown(background_tasks: BackgroundTasks) -> dict[str, str]:
        if shutdown_callback is not None:
            background_tasks.add_task(shutdown_callback)
        return {"status": "stopping"}

    return app
