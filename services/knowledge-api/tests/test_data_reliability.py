from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from knowledge_api.app import create_app
from knowledge_api.storage import CURRENT_SCHEMA_VERSION, KnowledgeStore


TOKEN = "test-runtime-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def api_client(data_dir: Path) -> httpx.AsyncClient:
    app = create_app(TOKEN, data_dir=data_dir, default_model_provider="extractive")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://knowledge-api.test")


def conversation_payload(conversation_id: str, title: str = "工资哪天发？") -> dict[str, object]:
    return {
        "id": conversation_id,
        "knowledge_base_id": "enterprise",
        "title": title,
        "pinned": False,
    }


def message_payload(message_id: str, content: str) -> dict[str, object]:
    return {
        "id": message_id,
        "role": "user",
        "content": content,
        "time": "10:30",
        "status": "complete",
    }


def test_schema_v4_upgrade_creates_snapshot_and_conversation_tables(tmp_path: Path) -> None:
    KnowledgeStore(tmp_path)
    database = tmp_path / "knowledge.db"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE metadata SET value = '4' WHERE key = 'schema_version'")
        connection.execute("DROP TABLE data_imports")
        connection.execute("DROP TABLE conversation_messages")
        connection.execute("DROP TABLE conversations")
        connection.execute("DROP TABLE schema_migrations")

    store = KnowledgeStore(tmp_path)

    assert store.schema_status() == {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "current_schema_version": CURRENT_SCHEMA_VERSION,
        "integrity": "ok",
    }
    assert list((tmp_path / "backups").glob("pre-upgrade-v4-to-v5-*.db"))
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"conversations", "conversation_messages", "data_imports", "schema_migrations"}.issubset(tables)


@pytest.mark.anyio
async def test_legacy_import_is_idempotent_and_conversations_have_crud(tmp_path: Path) -> None:
    payload = {
        "import_id": "webview-localstorage-v1",
        "conversations": [conversation_payload("legacy-1")],
        "messages": {"legacy-1": [message_payload("legacy-message-1", "工资哪天发？")]},
    }
    async with api_client(tmp_path) as client:
        first = await client.post("/api/v1/conversations/import", headers=HEADERS, json=payload)
        second = await client.post("/api/v1/conversations/import", headers=HEADERS, json=payload)
        listed = await client.get("/api/v1/conversations", headers=HEADERS)
        messages = await client.get("/api/v1/conversations/legacy-1/messages", headers=HEADERS)
        updated = await client.put(
            "/api/v1/conversations/legacy-1",
            headers=HEADERS,
            json={"title": "发薪时间", "pinned": True},
        )
        replaced = await client.put(
            "/api/v1/conversations/legacy-1/messages",
            headers=HEADERS,
            json={"items": [message_payload("replacement-message", "替换后的问题")]},
        )
        deleted = await client.delete("/api/v1/conversations/legacy-1", headers=HEADERS)
        missing = await client.get("/api/v1/conversations/legacy-1/messages", headers=HEADERS)

    assert first.status_code == 200 and first.json()["imported"] is True
    assert second.status_code == 200 and second.json()["imported"] is False
    assert [item["id"] for item in listed.json()["items"]] == ["legacy-1"]
    assert messages.json()["items"][0]["content"] == "工资哪天发？"
    assert updated.json()["conversation"]["title"] == "发薪时间"
    assert updated.json()["conversation"]["pinned"] is True
    assert replaced.json()["items"][0]["content"] == "替换后的问题"
    assert deleted.status_code == 204
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_backup_restore_validates_and_restores_conversations(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    original_path = tmp_path / "documents" / "enterprise" / "policy.md"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_text("工资每月15日发放。", encoding="utf-8")
    store.create_document(
        knowledge_base_id="enterprise",
        source_name="policy.md",
        source_type="md",
        object_path="documents/enterprise/policy.md",
        content_hash="test-hash",
        size_bytes=original_path.stat().st_size,
        mime_type="text/markdown",
    )
    async with api_client(tmp_path) as client:
        created = await client.post("/api/v1/conversations", headers=HEADERS, json=conversation_payload("before-backup"))
        backup_response = await client.post("/api/v1/storage/backups", headers=HEADERS)
        after = await client.post("/api/v1/conversations", headers=HEADERS, json=conversation_payload("after-backup", "不应保留"))
        backup_id = backup_response.json()["backup"]["id"]
        restored = await client.post(f"/api/v1/storage/backups/{backup_id}/restore", headers=HEADERS)
        listed = await client.get("/api/v1/conversations", headers=HEADERS)
        status = await client.get("/api/v1/storage/status", headers=HEADERS)
        backups = await client.get("/api/v1/storage/backups", headers=HEADERS)

    assert created.status_code == 201 and after.status_code == 201
    assert backup_response.status_code == 201
    assert restored.status_code == 200
    assert restored.json()["restored"] is True
    assert restored.json()["safety_backup_id"]
    assert [item["id"] for item in listed.json()["items"]] == ["before-backup"]
    assert original_path.read_text(encoding="utf-8") == "工资每月15日发放。"
    assert status.json()["schema"]["integrity"] == "ok"
    assert len(backups.json()["items"]) == 2


@pytest.mark.anyio
async def test_corrupt_backup_is_rejected_without_being_saved(tmp_path: Path) -> None:
    async with api_client(tmp_path) as client:
        response = await client.post(
            "/api/v1/storage/backups/import",
            headers=HEADERS,
            files={"backup": ("broken.zhiyu-backup", b"not-a-zip", "application/octet-stream")},
        )
        backups = await client.get("/api/v1/storage/backups", headers=HEADERS)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BACKUP_INVALID"
    assert backups.json()["items"] == []
