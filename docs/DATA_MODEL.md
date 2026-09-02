# 数据模型设计

## 1. 设计原则

- 业务主键使用稳定 UUID，不依赖文件绝对路径。
- 原文、标准化内容、块、向量和引用之间可追溯。
- 文档和索引采用版本化设计。
- 删除、重试和重新索引保持幂等。
- 企业模式的业务表包含 `tenant_id`，所有查询强制租户隔离。

## 2. 核心实体

### 2.1 knowledge_bases

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 知识库主键 |
| tenant_id | UUID/NULL | 企业租户；本地版为空 |
| name | TEXT | 名称 |
| description | TEXT | 描述 |
| embedding_model_id | TEXT | 索引模型 |
| chunking_profile | JSON | 切分策略 |
| retrieval_profile | JSON | 检索策略 |
| status | TEXT | active/archived/deleting |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

### 2.2 documents

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 文档主键 |
| knowledge_base_id | UUID | 所属知识库 |
| source_name | TEXT | 原始文件名 |
| source_type | TEXT | pdf/docx/xlsx 等 |
| object_path | TEXT | 应用数据目录内相对路径 |
| content_hash | TEXT | SHA-256 |
| size_bytes | INTEGER | 文件大小 |
| mime_type | TEXT | MIME 类型 |
| language | TEXT | 主要语言 |
| active_version | INTEGER | 当前活动版本 |
| status | TEXT | queued/processing/ready/failed/disabled |
| error_code | TEXT/NULL | 稳定错误码 |
| error_message | TEXT/NULL | 面向用户的错误说明 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

### 2.3 document_versions

记录每次解析和索引版本，包括解析器版本、OCR 配置、切分版本、Embedding 模型、块数量和状态。新版本完成前不替换活动版本。

### 2.4 chunks

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 文本块主键 |
| document_id | UUID | 文档 |
| document_version | INTEGER | 文档版本 |
| chunk_index | INTEGER | 文档内顺序 |
| content_kind | TEXT | text/table/ocr 证据类型 |
| parent_chunk_id | UUID/NULL | 父子块关系 |
| parent_content | TEXT | 仅用于回答和来源展示的有界父上下文 |
| content | TEXT | 标准化文本 |
| content_hash | TEXT | 内容哈希 |
| token_count | INTEGER | token 数 |
| heading_path | JSON | 标题层级 |
| page_start/page_end | INTEGER/NULL | 页码范围 |
| location | JSON | 坐标、行号或单元格范围 |
| metadata | JSON | 标签与解析信息 |
| created_at | DATETIME | 创建时间 |

向量本体由 VectorStore 保存，使用 `chunk_id`、`tenant_id`、`knowledge_base_id`、`document_id` 和版本作为过滤字段。

当前单机 SQLite schema 版本为 `5`。现有 `chunks` 表使用 `page_start`、`heading`、`content_kind`、`parent_chunk_id`、`parent_content` 和 `location_json`；`location_json` 可保存 `pdf-bbox`、`pdf-page`、`slide` 或 `worksheet`。父 ID 由文档 ID 与父窗口序号稳定派生，同一父窗口内的多个子块共享父 ID。当前向量仍以内嵌 JSON 保存，外部 VectorStore 是企业规模化阶段的替换目标。

### 2.5 ingestion_jobs

| 字段 | 类型 | 说明 |
|---|---|---|
| id | UUID | 任务主键 |
| document_id | UUID | 关联文档 |
| stage | TEXT | validate/parse/ocr/chunk/embed/index/verify |
| status | TEXT | pending/running/succeeded/failed/canceled |
| progress | REAL | 0–1 |
| attempt | INTEGER | 重试次数 |
| lease_until | DATETIME/NULL | Worker 租约 |
| error_code | TEXT/NULL | 错误码 |
| error_detail | JSON/NULL | 脱敏诊断信息 |
| created_at | DATETIME | 创建时间 |
| started_at/finished_at | DATETIME/NULL | 执行时间 |

### 2.6 conversations 与 messages

单机版从 schema v5 起将对话和消息持久化到 `conversations` 与 `conversation_messages`。消息按 `position` 保序，保存角色、正文、显示时间、状态、反馈、推荐追问和引用快照；删除对话通过外键级联删除消息。`data_imports` 记录 localStorage 迁移批次，保证首次升级可重复调用但只导入一次。企业版后续可将引用快照规范化为 `message_citations` 并关联确定的文档版本。

### 2.7 permissions

企业版使用主体（用户、组、角色）到资源（知识库、文档、标签）的授权关系。检索过滤器由权限服务生成，不接受客户端直接传入的授权结果。

## 3. 状态机

文档状态：

```text
queued → processing → ready
               ├─→ failed → queued（重试）
ready → disabled → ready
任意非删除状态 → deleting → deleted
```

任务状态：

```text
pending → running → succeeded
                  ├─→ failed
                  └─→ canceled
```

## 4. 数据完整性

- `documents(knowledge_base_id, content_hash)` 建立可配置的去重约束。
- 块必须唯一关联一个文档版本。
- 活动文档版本必须处于 `ready`。
- 引用必须关联真实存在的消息、块与文档版本。
- 知识库删除采用两阶段流程，先禁止查询，再清理物理数据。

## 5. 迁移与兼容

- 每个数据库包含 `schema_version`。
- 当前迁移目标版本为 `5`；`schema_migrations` 记录已执行版本，v5 增加会话、消息与幂等导入表。
- 检测到旧版数据库时，应用会先使用 SQLite Online Backup 创建 `pre-upgrade` 快照；迁移失败时恢复快照并终止启动。
- 迁移脚本必须可重复检测、不可重复执行。
- 向量索引格式、Embedding 模型或标准化规则变化时，使用索引版本而不是原地覆盖。
