# API 设计规范

## 1. 基本约定

- API 前缀：`/api/v1`
- 内容类型：`application/json; charset=utf-8`
- 时间：ISO 8601 UTC
- 主键：UUID 字符串
- 分页：游标优先，管理列表可使用页码
- 错误：稳定错误码、用户可读消息和关联 ID
- 本地模式仍需认证，禁止无保护的环回 HTTP 服务

## 2. 身份认证

本地桌面壳启动 API 时生成短期随机令牌，通过受控启动通道传递。前端请求使用：

```http
Authorization: Bearer <ephemeral-token>
```

企业模式使用 OIDC/OAuth 2.0 或企业 SSO。密码、token、文档内容和问题不得放入 URL 查询参数。

## 3. 错误格式

```json
{
  "error": {
    "code": "DOCUMENT_PARSE_FAILED",
    "message": "文档解析失败，请检查文件是否损坏。",
    "request_id": "req_...",
    "details": {
      "document_id": "..."
    }
  }
}
```

`details` 不得包含密码、完整 token、模型密钥或未脱敏的敏感原文。

## 4. 核心端点

### 4.1 知识库

```text
GET    /api/v1/knowledge-bases
```

当前单机版提供知识库列表；创建、详情、重命名、归档和删除属于下一阶段生命周期 API，尚未对外暴露。

创建请求示例：

```json
{
  "name": "产品资料库",
  "description": "产品说明书与技术规范",
  "embedding_model_id": "bge-m3",
  "chunking_profile": "structure-default"
}
```

### 4.2 文档

```text
POST   /api/v1/knowledge-bases/{id}/documents
GET    /api/v1/knowledge-bases/{id}/documents
DELETE /api/v1/documents/{id}
POST   /api/v1/documents/{id}/retry
POST   /api/v1/knowledge-bases/{id}/reindex
GET    /api/v1/documents/{document_id}/chunks/{chunk_id}/preview
```

本地上传可使用 `multipart/form-data`。大文件和企业对象存储使用分片上传与完成确认。

当前上传白名单为 PDF、DOCX、XLS/XLSX、PPTX、HTML/HTM、TXT、MD/Markdown。批量上传会先完整校验格式，发现不支持的文件时整批拒绝并返回具体文件名，避免出现部分文件已写入但界面只显示一次失败的情况。PDF 引用预览验证文档与块的归属，从受控本地存储读取原文，在内存中按 `location.bbox` 绘制灰色高亮并返回 `image/png`；响应使用 `Cache-Control: no-store`。非 PDF 返回 `415 DOCUMENT_PREVIEW_UNSUPPORTED`，位置或归属无效返回 `404 DOCUMENT_LOCATION_NOT_FOUND`。

`POST /api/v1/chat/completions` 接受可选的 `conversation_id` 与最多 12 条 `history`（服务实际使用最近 10 条）。历史只用于追问消歧，不作为事实依据；检索和回答仍受当前 `knowledge_base_ids` 与 `document_ids` 范围约束。响应为原生 `application/x-ndjson` 流。

### 4.3 任务

```text
GET  /api/v1/jobs
GET  /api/v1/jobs/{id}
```

任务取消、持久化调度器和事件推送尚未实现；当前进度由桌面端轮询持久化任务记录。

后续持久化调度器的目标事件推送格式如下：

```json
{
  "event": "job.progress",
  "job_id": "...",
  "stage": "embedding",
  "progress": 0.68,
  "message": "正在生成向量 340/500"
}
```

### 4.4 会话与消息

```text
GET    /api/v1/conversations
POST   /api/v1/conversations
PUT    /api/v1/conversations/{id}
DELETE /api/v1/conversations/{id}
GET    /api/v1/conversations/{id}/messages
PUT    /api/v1/conversations/{id}/messages
POST   /api/v1/conversations/import
```

桌面版会在首次升级时将 `zhiyu-conversations` 与 `zhiyu-conversation-messages` 幂等导入 SQLite。导入事务使用固定 `import_id` 防止重复执行；旧 localStorage 数据保留为迁移安全副本，但不再作为桌面版运行时数据源。

### 4.5 存储、备份与恢复

```text
GET  /api/v1/storage/status
GET  /api/v1/storage/backups
POST /api/v1/storage/backups
POST /api/v1/storage/backups/import
POST /api/v1/storage/backups/{id}/restore
POST /api/v1/storage/backups/open-folder
```

`.zhiyu-backup` 包含数据库、原始文档和带 SHA-256 的版本化清单。恢复前检查路径穿越、条目数量、解压体积、文件大小、哈希、SQLite `quick_check`、数据表、schema 版本和文档完整性；真正替换前自动创建 `pre-restore` 安全备份，失败时回滚原数据库与文档目录。

### 4.6 流式回答诊断

`POST /api/v1/chat/completions` 使用 NDJSON 依次返回 `message.start`、`retrieval.completed`、`citation`、`message.delta` 与 `message.completed`。起止事件包含不携带正文的本地诊断信息：

- `timings_ms`：问题规划、检索、回答准备、首段可见、生成和总耗时。
- `diagnostics`：上下文判定、是否改写、检索路数和候选量、Embedding 运行方式、回答路径、引用校验重试、原文回退、模型加载耗时、token 数与生成速度。

诊断字段只描述运行阶段和计数，不包含用户问题、对话历史、检索片段或文档内容。前端默认收起显示，当前阶段不持久化到 SQLite。

### 4.7 检索

```text
POST /api/v1/retrieval/search
```

```json
{
  "query": "软件支持哪些操作系统？",
  "knowledge_base_ids": ["..."],
  "filters": {
    "document_types": ["pdf", "docx", "xls", "xlsx", "txt", "md"],
    "tags": ["正式发布"]
  },
  "limit": 10,
  "debug": false
}
```

当前 `POST /api/v1/retrieval/search` 还会返回 `index_mode`、`embedding` 与 `reranker` 状态。当前 `index_mode` 为 `hybrid-rrf-rerank-v1`；每个命中项可包含 `parent_chunk_id`、`parent_content`、`content_kind`、`location`、`vector_score`、`lexical_score`、`support_factor`、`retrieval_score`、`rerank_score`、`evidence_score`、`retrieval_mode`、`embedding_model_id` 和 `reranker_model_id`，用于检索诊断。`content` 是参与召回的精确子块，`parent_content` 只提供回答上下文；同一父块的多个结果会在重排后去重。

### 4.8 问答

```text
POST /api/v1/chat/completions
```

请求示例：

```json
{
  "conversation_id": null,
  "knowledge_base_ids": ["..."],
  "message": "根据安装手册，最低内存要求是什么？",
  "model_id": "local-default",
  "stream": true
}
```

使用 `fetch()` 读取流式响应，避免 EventSource 只能使用 GET 而导致敏感数据进入 URL。建议事件类型：

```text
message.start
retrieval.completed
message.delta
citation
message.completed
error
```

当前本地实现使用换行分隔 JSON（`application/x-ndjson`）返回上述事件。`citation` 事件先于包含 `[[n]]` 标记的正文增量发送，前端在完成事件后将标记解析为可点击来源。

`citation` 包含精确 `chunk_id`、`parent_chunk_id`、文档 ID、文档名、格式、页码、章节、`excerpt`、父块 `context`、`content_kind` 和 `location`。PDF 的 `location` 示例：

```json
{
  "kind": "pdf-bbox",
  "page": 1,
  "bbox": [72.0, 108.0, 420.0, 132.0],
  "page_width": 595.0,
  "page_height": 842.0
}
```

完成事件示例：

```json
{
  "event": "message.completed",
  "message_id": "...",
  "conversation_id": "...",
  "finish_reason": "stop",
  "usage": {
    "input_tokens": 1240,
    "output_tokens": 210
  }
}
```

### 4.9 模型

```text
GET  /api/v1/model/settings
PUT  /api/v1/model/settings
POST /api/v1/model/detect
```

设置示例：

```json
{
  "provider": "ollama",
  "base_url": "http://127.0.0.1:11434",
  "model": "qwen3:8b",
  "timeout_seconds": 90
}
```

`provider` 可取 `auto`、`ollama`、`openai-compatible` 或 `extractive`。当前个人本地模式只接受 HTTP 回环地址，不接受公网 URL、URL 凭据或查询参数。检测结果区分 `ready`、`service-unavailable`、`model-missing`、`extractive` 和 `error`。

`GET /api/v1/runtime/status` 同时返回回答模型、Embedding、Reranker、RapidOCR 状态、`index_mode` 和按模型/维度/版本聚合的 `indexes` 摘要。回答模型与 Embedding 都返回 `execution_mode`，用于区分正常自动选择和 CPU 兼容模式；OCR 明确报告纯本地 CPU 状态。回答模型不可用不影响检索与严格原文回答，因此 Knowledge API 本身仍保持 `running`。

## 5. 幂等与并发

- 上传完成、重建索引和删除操作支持 `Idempotency-Key`。
- 更新资源使用版本号或 ETag 防止覆盖并发修改。
- 长任务立即返回 `202 Accepted` 和任务资源地址。
- API 只接受资源标识，不接受客户端提供的任意本地文件路径。

## 6. 版本策略

破坏性变化通过新的主版本路径发布。响应可以增加可选字段，但不得改变既有字段含义。桌面壳启动时检查 API 协议版本并在不兼容时给出明确升级提示。
