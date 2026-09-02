from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import NAMESPACE_URL, uuid4, uuid5


CURRENT_SCHEMA_VERSION = 5


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class KnowledgeStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.documents_dir = self.data_dir / "documents"
        self.backups_dir = self.data_dir / "backups"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.data_dir / "knowledge.db"
        previous_version = self._existing_schema_version()
        upgrade_backup = self._create_upgrade_snapshot(previous_version) if 0 < previous_version < CURRENT_SCHEMA_VERSION else None
        try:
            self._initialize()
        except Exception:
            if upgrade_backup is not None:
                self._restore_database_snapshot(upgrade_backup)
            raise

    def _existing_schema_version(self) -> int:
        if not self.database_path.exists() or self.database_path.stat().st_size == 0:
            return 0
        try:
            connection = sqlite3.connect(f"file:{self.database_path.as_posix()}?mode=ro", uri=True)
            try:
                row = connection.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
                return int(row[0]) if row else 0
            finally:
                connection.close()
        except (sqlite3.Error, ValueError, OSError):
            return 0

    def _create_upgrade_snapshot(self, previous_version: int) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = self.backups_dir / f"pre-upgrade-v{previous_version}-to-v{CURRENT_SCHEMA_VERSION}-{stamp}.db"
        source = sqlite3.connect(self.database_path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return destination

    def _restore_database_snapshot(self, snapshot: Path) -> None:
        source = sqlite3.connect(snapshot)
        target = sqlite3.connect(self.database_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_bases (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                    source_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    object_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    mime_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    vector TEXT NOT NULL,
                    page_start INTEGER,
                    heading TEXT NOT NULL DEFAULT '',
                    content_kind TEXT NOT NULL DEFAULT 'text',
                    parent_chunk_id TEXT NOT NULL DEFAULT '',
                    parent_content TEXT NOT NULL DEFAULT '',
                    location_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_kb ON chunks(knowledge_base_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_updated ON ingestion_jobs(updated_at DESC);
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._ensure_column(connection, "documents", "index_model_id", "TEXT")
            self._ensure_column(connection, "documents", "index_version", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(connection, "chunks", "embedding_model_id", "TEXT NOT NULL DEFAULT 'hashing-vector-v1'")
            self._ensure_column(connection, "chunks", "embedding_dimensions", "INTEGER NOT NULL DEFAULT 256")
            self._ensure_column(connection, "chunks", "index_version", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(connection, "chunks", "content_kind", "TEXT NOT NULL DEFAULT 'text'")
            self._ensure_column(connection, "chunks", "parent_chunk_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "chunks", "parent_content", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "chunks", "location_json", "TEXT")
            self._initialize_fts(connection)
            self._apply_schema_migrations(connection)
            now = utc_now()
            connection.executemany(
                "INSERT OR IGNORE INTO knowledge_bases(id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                [
                    ("enterprise", "企业制度库", "企业规章、制度与流程", now, now),
                    ("project", "项目资料", "项目文档与交付资料", now, now),
                    ("personal", "我的文档", "个人本地资料", now, now),
                ],
            )

    @staticmethod
    def _apply_schema_migrations(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
            )"""
        )
        row = connection.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
        existing_version = int(row[0]) if row else 4
        if existing_version >= 4:
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at, description) VALUES (4, ?, ?)",
                (utc_now(), "既有知识库、文档、索引与任务基线"),
            )
        if existing_version < 5:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    knowledge_base_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_kb_updated
                    ON conversations(knowledge_base_id, pinned DESC, updated_at DESC);
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    time_label TEXT NOT NULL DEFAULT '',
                    citations_json TEXT,
                    suggestions_json TEXT,
                    status TEXT,
                    feedback TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(conversation_id, position)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON conversation_messages(conversation_id, position);
                CREATE TABLE IF NOT EXISTS data_imports (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    item_count INTEGER NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at, description) VALUES (5, ?, ?)",
                (utc_now(), "会话、消息与幂等导入记录迁入 SQLite"),
            )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('schema_version', ?)",
            (str(CURRENT_SCHEMA_VERSION),),
        )

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _initialize_fts(connection: sqlite3.Connection) -> None:
        try:
            connection.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED, knowledge_base_id UNINDEXED, document_id UNINDEXED,
                content, heading, tokenize='trigram'
                )"""
            )
            tokenizer = "trigram"
        except sqlite3.OperationalError:
            connection.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED, knowledge_base_id UNINDEXED, document_id UNINDEXED,
                content, heading, tokenize='unicode61'
                )"""
            )
            tokenizer = "unicode61"
        connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES ('fts_tokenizer', ?)", (tokenizer,))
        connection.execute(
            """INSERT INTO chunks_fts(chunk_id, knowledge_base_id, document_id, content, heading)
            SELECT c.id, c.knowledge_base_id, c.document_id, c.content, c.heading
            FROM chunks c
            WHERE NOT EXISTS (SELECT 1 FROM chunks_fts f WHERE f.chunk_id = c.id)"""
        )

    def list_knowledge_bases(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT kb.*, COUNT(d.id) AS document_count
                FROM knowledge_bases kb
                LEFT JOIN documents d ON d.knowledge_base_id = kb.id
                GROUP BY kb.id
                ORDER BY CASE kb.id WHEN 'enterprise' THEN 1 WHEN 'project' THEN 2 WHEN 'personal' THEN 3 ELSE 4 END, kb.created_at
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_knowledge_base(self, knowledge_base_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM knowledge_bases WHERE id = ?", (knowledge_base_id,)).fetchone()
        return dict(row) if row else None

    def create_document(self, *, knowledge_base_id: str, source_name: str, source_type: str, object_path: str, content_hash: str, size_bytes: int, mime_type: str) -> dict[str, Any]:
        document_id = str(uuid4())
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO documents(id, knowledge_base_id, source_name, source_type, object_path, content_hash, size_bytes, mime_type, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""",
                (document_id, knowledge_base_id, source_name, source_type, object_path, content_hash, size_bytes, mime_type, now, now),
            )
        return self.get_document(document_id) or {}

    def list_documents(self, knowledge_base_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM documents WHERE knowledge_base_id = ? ORDER BY created_at DESC", (knowledge_base_id,)).fetchall()
        return [dict(row) for row in rows]

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return dict(row) if row else None

    def update_document(self, document_id: str, *, status: str, chunk_count: int | None = None, error_code: str | None = None, error_message: str | None = None) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """UPDATE documents SET status = ?, chunk_count = COALESCE(?, chunk_count), error_code = ?, error_message = ?, updated_at = ? WHERE id = ?""",
                (status, chunk_count, error_code, error_message, now, document_id),
            )
            document = connection.execute("SELECT knowledge_base_id FROM documents WHERE id = ?", (document_id,)).fetchone()
            if document:
                connection.execute("UPDATE knowledge_bases SET updated_at = ? WHERE id = ?", (now, document["knowledge_base_id"]))

    def delete_document(self, document_id: str) -> str | None:
        document = self.get_document(document_id)
        if not document:
            return None
        with self._connect() as connection:
            connection.execute("DELETE FROM chunks_fts WHERE document_id = ?", (document_id,))
            connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            connection.execute("UPDATE knowledge_bases SET updated_at = ? WHERE id = ?", (utc_now(), document["knowledge_base_id"]))
        return str(document["object_path"])

    def replace_chunks(
        self,
        document_id: str,
        chunks: list[dict[str, Any]],
        *,
        embedding_model_id: str = "hashing-vector-v1",
        embedding_dimensions: int = 256,
        index_version: int = 1,
    ) -> None:
        document = self.get_document(document_id)
        if not document:
            raise ValueError("document does not exist")
        now = utc_now()
        records = [
            (
                str(uuid4()), document_id, document["knowledge_base_id"], index, chunk["content"],
                json.dumps(chunk["vector"], separators=(",", ":")), chunk.get("page_start"), chunk.get("heading", ""),
                chunk.get("content_kind", "text"),
                str(uuid5(NAMESPACE_URL, f"zhiyu:{document_id}:parent:{chunk.get('parent_index', index)}")),
                chunk.get("parent_content", chunk["content"]),
                json.dumps(chunk.get("location"), ensure_ascii=False, separators=(",", ":")) if chunk.get("location") else None,
                embedding_model_id, embedding_dimensions, index_version, now,
            )
            for index, chunk in enumerate(chunks)
        ]
        with self._connect() as connection:
            connection.execute("DELETE FROM chunks_fts WHERE document_id = ?", (document_id,))
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            connection.executemany(
                """INSERT INTO chunks(
                id, document_id, knowledge_base_id, chunk_index, content, vector, page_start, heading,
                content_kind, parent_chunk_id, parent_content, location_json,
                embedding_model_id, embedding_dimensions, index_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                records,
            )
            connection.executemany(
                "INSERT INTO chunks_fts(chunk_id, knowledge_base_id, document_id, content, heading) VALUES (?, ?, ?, ?, ?)",
                [(record[0], record[2], record[1], record[4], record[7]) for record in records],
            )
            connection.execute(
                "UPDATE documents SET index_model_id = ?, index_version = ?, updated_at = ? WHERE id = ?",
                (embedding_model_id, index_version, now, document_id),
            )

    def list_search_chunks(self, knowledge_base_ids: list[str], document_ids: list[str] | None = None) -> list[dict[str, Any]]:
        if not knowledge_base_ids:
            return []
        knowledge_placeholders = ",".join("?" for _ in knowledge_base_ids)
        parameters: list[str] = list(knowledge_base_ids)
        document_clause = ""
        if document_ids:
            document_placeholders = ",".join("?" for _ in document_ids)
            document_clause = f" AND c.document_id IN ({document_placeholders})"
            parameters.extend(document_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT c.*, d.source_name, d.source_type, d.updated_at AS document_updated_at
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.knowledge_base_id IN ({knowledge_placeholders}) AND d.status = 'ready'{document_clause}""",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT c.*, d.source_name, d.source_type, d.object_path, d.updated_at AS document_updated_at
                FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id = ?""",
                (chunk_id,),
            ).fetchone()
        return dict(row) if row else None

    def search_fts(
        self,
        match_query: str,
        knowledge_base_ids: list[str],
        document_ids: list[str] | None = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        if not match_query or not knowledge_base_ids:
            return []
        knowledge_placeholders = ",".join("?" for _ in knowledge_base_ids)
        parameters: list[Any] = [match_query, *knowledge_base_ids]
        document_clause = ""
        if document_ids:
            document_placeholders = ",".join("?" for _ in document_ids)
            document_clause = f" AND c.document_id IN ({document_placeholders})"
            parameters.extend(document_ids)
        parameters.append(max(1, min(limit, 200)))
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"""SELECT f.chunk_id, bm25(chunks_fts, 0.0, 0.0, 0.0, 1.0, 1.4) AS fts_rank
                    FROM chunks_fts f
                    JOIN chunks c ON c.id = f.chunk_id
                    JOIN documents d ON d.id = c.document_id
                    WHERE chunks_fts MATCH ?
                    AND c.knowledge_base_id IN ({knowledge_placeholders})
                    AND d.status = 'ready'{document_clause}
                    ORDER BY fts_rank ASC LIMIT ?""",
                    parameters,
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in rows]

    def index_summary(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT embedding_model_id, embedding_dimensions, index_version,
                COUNT(*) AS chunk_count, COUNT(DISTINCT document_id) AS document_count
                FROM chunks GROUP BY embedding_model_id, embedding_dimensions, index_version
                ORDER BY chunk_count DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def create_job(self, document_id: str, attempt: int = 1) -> dict[str, Any]:
        job_id = str(uuid4())
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ingestion_jobs(id, document_id, stage, status, progress, attempt, created_at, updated_at)
                VALUES (?, ?, 'queued', 'pending', 0, ?, ?, ?)""",
                (job_id, document_id, attempt, now, now),
            )
        return self.get_job(job_id) or {}

    def update_job(self, job_id: str, *, stage: str, status: str, progress: int, error_code: str | None = None, error_message: str | None = None) -> None:
        now = utc_now()
        started_at = now if status == "running" else None
        finished_at = now if status in {"succeeded", "failed", "canceled"} else None
        with self._connect() as connection:
            connection.execute(
                """UPDATE ingestion_jobs SET stage = ?, status = ?, progress = ?, error_code = ?, error_message = ?, started_at = COALESCE(started_at, ?), finished_at = COALESCE(?, finished_at), updated_at = ? WHERE id = ?""",
                (stage, status, progress, error_code, error_message, started_at, finished_at, now, job_id),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT j.*, d.source_name, d.source_type, d.knowledge_base_id FROM ingestion_jobs j JOIN documents d ON d.id = j.document_id WHERE j.id = ?""",
                (job_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT j.*, d.source_name, d.source_type, d.knowledge_base_id FROM ingestion_jobs j JOIN documents d ON d.id = j.document_id ORDER BY j.updated_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def active_job_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM ingestion_jobs WHERE status IN ('pending', 'running')"
            ).fetchone()
        return int(row["count"]) if row else 0

    def latest_attempt(self, document_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COALESCE(MAX(attempt), 0) AS attempt FROM ingestion_jobs WHERE document_id = ?", (document_id,)).fetchone()
        return int(row["attempt"]) if row else 0

    def list_conversations(self, knowledge_base_id: str | None = None) -> list[dict[str, Any]]:
        parameters: tuple[str, ...] = ()
        clause = ""
        if knowledge_base_id:
            clause = " WHERE knowledge_base_id = ?"
            parameters = (knowledge_base_id,)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM conversations{clause} ORDER BY pinned DESC, updated_at DESC",
                parameters,
            ).fetchall()
        return [{**dict(row), "pinned": bool(row["pinned"])} for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        return {**dict(row), "pinned": bool(row["pinned"])} if row else None

    def create_conversation(
        self,
        *,
        conversation_id: str,
        knowledge_base_id: str,
        title: str,
        pinned: bool = False,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO conversations(id, knowledge_base_id, title, pinned, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    knowledge_base_id = excluded.knowledge_base_id,
                    title = excluded.title,
                    pinned = excluded.pinned,
                    updated_at = excluded.updated_at""",
                (conversation_id, knowledge_base_id, title.strip(), int(pinned), now, now),
            )
        return self.get_conversation(conversation_id) or {}

    def update_conversation(self, conversation_id: str, *, title: str | None = None, pinned: bool | None = None) -> dict[str, Any] | None:
        current = self.get_conversation(conversation_id)
        if not current:
            return None
        next_title = title.strip() if title is not None else str(current["title"])
        next_pinned = bool(pinned) if pinned is not None else bool(current["pinned"])
        with self._connect() as connection:
            connection.execute(
                "UPDATE conversations SET title = ?, pinned = ?, updated_at = ? WHERE id = ?",
                (next_title, int(next_pinned), utc_now(), conversation_id),
            )
        return self.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cursor.rowcount > 0

    @staticmethod
    def _message_record(conversation_id: str, position: int, message: dict[str, Any], now: str) -> tuple[Any, ...]:
        return (
            str(message["id"]),
            conversation_id,
            position,
            str(message["role"]),
            str(message.get("content", "")),
            str(message.get("time", "")),
            json.dumps(message.get("citations"), ensure_ascii=False, separators=(",", ":")) if message.get("citations") is not None else None,
            json.dumps(message.get("suggestions"), ensure_ascii=False, separators=(",", ":")) if message.get("suggestions") is not None else None,
            message.get("status"),
            message.get("feedback"),
            now,
            now,
        )

    def replace_conversation_messages(self, conversation_id: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.get_conversation(conversation_id):
            raise ValueError("conversation does not exist")
        now = utc_now()
        records = [self._message_record(conversation_id, position, message, now) for position, message in enumerate(messages)]
        with self._connect() as connection:
            connection.execute("DELETE FROM conversation_messages WHERE conversation_id = ?", (conversation_id,))
            connection.executemany(
                """INSERT INTO conversation_messages(
                id, conversation_id, position, role, content, time_label, citations_json, suggestions_json,
                status, feedback, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                records,
            )
            connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        return self.list_conversation_messages(conversation_id)

    def list_conversation_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY position",
                (conversation_id,),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "role": str(row["role"]),
                "content": str(row["content"]),
                "time": str(row["time_label"]),
                "citations": json.loads(row["citations_json"]) if row["citations_json"] else None,
                "suggestions": json.loads(row["suggestions_json"]) if row["suggestions_json"] else None,
                "status": row["status"],
                "feedback": row["feedback"],
            }
            for row in rows
        ]

    def import_legacy_conversations(
        self,
        *,
        import_id: str,
        conversations: list[dict[str, Any]],
        messages_by_conversation: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as connection:
            previous = connection.execute("SELECT * FROM data_imports WHERE id = ?", (import_id,)).fetchone()
            if previous:
                return {"imported": False, "conversation_count": int(previous["item_count"]), "imported_at": str(previous["imported_at"])}

            known_bases = {str(row["id"]) for row in connection.execute("SELECT id FROM knowledge_bases").fetchall()}
            for conversation in conversations:
                knowledge_base_id = str(conversation["knowledge_base_id"])
                if knowledge_base_id not in known_bases:
                    raise ValueError(f"unknown knowledge base: {knowledge_base_id}")
                conversation_id = str(conversation["id"])
                connection.execute(
                    """INSERT INTO conversations(id, knowledge_base_id, title, pinned, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING""",
                    (
                        conversation_id,
                        knowledge_base_id,
                        str(conversation["title"]).strip(),
                        int(bool(conversation.get("pinned"))),
                        now,
                        now,
                    ),
                )
                existing_messages = connection.execute(
                    "SELECT COUNT(*) AS count FROM conversation_messages WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
                if existing_messages and int(existing_messages["count"]) > 0:
                    continue
                records = [
                    self._message_record(conversation_id, position, message, now)
                    for position, message in enumerate(messages_by_conversation.get(conversation_id, []))
                ]
                connection.executemany(
                    """INSERT INTO conversation_messages(
                    id, conversation_id, position, role, content, time_label, citations_json, suggestions_json,
                    status, feedback, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    records,
                )
            connection.execute(
                "INSERT INTO data_imports(id, source, imported_at, item_count) VALUES (?, 'webview-localstorage', ?, ?)",
                (import_id, now, len(conversations)),
            )
        return {"imported": True, "conversation_count": len(conversations), "imported_at": now}

    def schema_status(self) -> dict[str, Any]:
        with self._connect() as connection:
            integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            row = connection.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
        return {
            "schema_version": int(row[0]) if row else 0,
            "current_schema_version": CURRENT_SCHEMA_VERSION,
            "integrity": integrity,
        }
