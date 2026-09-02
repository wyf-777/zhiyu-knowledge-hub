from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .storage import CURRENT_SCHEMA_VERSION, KnowledgeStore


BACKUP_FORMAT = "zhiyu-local-backup"
BACKUP_FORMAT_VERSION = 1
MAX_BACKUP_ENTRIES = 100_000
MAX_BACKUP_UNCOMPRESSED_BYTES = 20 * 1024 * 1024 * 1024
MAX_BACKUP_ENTRY_BYTES = 10 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000


class BackupValidationError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    normalized = PurePosixPath(name)
    if normalized.is_absolute() or not normalized.parts or any(part in {"", ".", ".."} for part in normalized.parts):
        raise BackupValidationError("备份包含不安全的文件路径。")
    return normalized


class BackupManager:
    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def _database_snapshot(self, destination: Path) -> None:
        source = sqlite3.connect(self.store.database_path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    def create_backup(self, *, reason: str = "manual") -> dict[str, Any]:
        if self.store.active_job_count() > 0:
            raise RuntimeError("仍有文档处理任务正在运行，请等待任务完成后再备份。")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_id = f"zhiyu-backup-{stamp}-{uuid4().hex[:8]}.zhiyu-backup"
        destination = self.store.backups_dir / backup_id
        stage = Path(tempfile.mkdtemp(prefix=".backup-stage-", dir=self.store.data_dir))
        try:
            database_snapshot = stage / "knowledge.db"
            self._database_snapshot(database_snapshot)
            self._validate_database(database_snapshot, self.store.documents_dir)
            entries: list[dict[str, Any]] = [
                {"path": "knowledge.db", "size": database_snapshot.stat().st_size, "sha256": _sha256(database_snapshot)}
            ]
            document_files = sorted(path for path in self.store.documents_dir.rglob("*") if path.is_file())
            for path in document_files:
                relative = path.relative_to(self.store.data_dir).as_posix()
                entries.append({"path": relative, "size": path.stat().st_size, "sha256": _sha256(path)})
            manifest = {
                "format": BACKUP_FORMAT,
                "format_version": BACKUP_FORMAT_VERSION,
                "created_at": _utc_now(),
                "reason": reason,
                "schema_version": self.store.schema_status()["schema_version"],
                "entries": entries,
            }
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                archive.write(database_snapshot, "knowledge.db")
                for path in document_files:
                    archive.write(path, path.relative_to(self.store.data_dir).as_posix())
            return self.describe_backup(destination, validate=True)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def list_backups(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.store.backups_dir.glob("*.zhiyu-backup"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                items.append(self.describe_backup(path, validate=False))
            except (BackupValidationError, OSError, zipfile.BadZipFile) as error:
                items.append({
                    "id": path.name,
                    "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
                    "size_bytes": path.stat().st_size,
                    "schema_version": None,
                    "document_file_count": 0,
                    "state": "invalid",
                    "message": str(error),
                })
        return items

    def describe_backup(self, path: Path, *, validate: bool) -> dict[str, Any]:
        manifest = self.validate_archive(path, verify_hashes=validate)
        return {
            "id": path.name,
            "created_at": str(manifest["created_at"]),
            "size_bytes": path.stat().st_size,
            "schema_version": int(manifest["schema_version"]),
            "document_file_count": sum(1 for entry in manifest["entries"] if str(entry["path"]).startswith("documents/")),
            "state": "valid",
            "message": "备份完整性校验通过。" if validate else "等待恢复前完整校验。",
        }

    def validate_archive(self, path: Path, *, verify_hashes: bool = True) -> dict[str, Any]:
        try:
            with zipfile.ZipFile(path, "r") as archive:
                infos = archive.infolist()
                if len(infos) > MAX_BACKUP_ENTRIES:
                    raise BackupValidationError("备份文件数量超过安全限制。")
                if sum(info.file_size for info in infos) > MAX_BACKUP_UNCOMPRESSED_BYTES:
                    raise BackupValidationError("备份解压后体积超过安全限制。")
                names = {info.filename for info in infos}
                for info in infos:
                    _safe_member(info.filename)
                    if info.file_size > MAX_BACKUP_ENTRY_BYTES:
                        raise BackupValidationError("备份包含超过单文件限制的内容。")
                    if info.compress_size > 0 and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                        raise BackupValidationError("备份压缩比异常，可能包含压缩炸弹。")
                if "manifest.json" not in names or "knowledge.db" not in names:
                    raise BackupValidationError("备份缺少清单或数据库文件。")
                if archive.testzip() is not None:
                    raise BackupValidationError("备份压缩数据已损坏。")
                manifest = json.loads(archive.read("manifest.json"))
                if manifest.get("format") != BACKUP_FORMAT or manifest.get("format_version") != BACKUP_FORMAT_VERSION:
                    raise BackupValidationError("不是受支持的知域备份格式。")
                schema_version = int(manifest.get("schema_version", 0))
                if schema_version < 1 or schema_version > CURRENT_SCHEMA_VERSION:
                    raise BackupValidationError("备份数据库版本高于当前应用，无法安全恢复。")
                entries = manifest.get("entries")
                if not isinstance(entries, list) or not entries:
                    raise BackupValidationError("备份清单为空。")
                listed_paths: set[str] = set()
                for entry in entries:
                    member = _safe_member(str(entry.get("path", ""))).as_posix()
                    if member in listed_paths or member not in names:
                        raise BackupValidationError("备份清单与压缩内容不一致。")
                    listed_paths.add(member)
                    info = archive.getinfo(member)
                    if int(entry.get("size", -1)) != info.file_size:
                        raise BackupValidationError(f"备份文件大小校验失败：{member}")
                    if verify_hashes:
                        digest = hashlib.sha256()
                        with archive.open(member) as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(chunk)
                        if digest.hexdigest() != str(entry.get("sha256", "")):
                            raise BackupValidationError(f"备份文件哈希校验失败：{member}")
                if "knowledge.db" not in listed_paths:
                    raise BackupValidationError("数据库未写入备份清单。")
                return manifest
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            if isinstance(error, BackupValidationError):
                raise
            raise BackupValidationError("备份清单无法解析。") from error
        except zipfile.BadZipFile as error:
            raise BackupValidationError("备份压缩包已损坏。") from error

    def import_backup(self, source: Path) -> dict[str, Any]:
        manifest = self.validate_archive(source, verify_hashes=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = self.store.backups_dir / f"zhiyu-import-{stamp}-{uuid4().hex[:8]}.zhiyu-backup"
        shutil.move(str(source), destination)
        return self.describe_backup(destination, validate=False) | {"message": f"已导入 {manifest['created_at']} 创建的备份。"}

    def _extract_validated(self, archive_path: Path, destination: Path) -> None:
        manifest = self.validate_archive(archive_path, verify_hashes=True)
        with zipfile.ZipFile(archive_path, "r") as archive:
            for entry in manifest["entries"]:
                member = _safe_member(str(entry["path"]))
                target = destination.joinpath(*member.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member.as_posix()) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)

    @staticmethod
    def _validate_database(database_path: Path, documents_root: Path) -> None:
        connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
                raise BackupValidationError("备份数据库完整性检查失败。")
            required = {"knowledge_bases", "documents", "chunks", "ingestion_jobs", "metadata"}
            tables = {str(row["name"]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not required.issubset(tables):
                raise BackupValidationError("备份数据库缺少必要数据表。")
            row = connection.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
            version = int(row[0]) if row else 0
            if version < 1 or version > CURRENT_SCHEMA_VERSION:
                raise BackupValidationError("备份数据库版本不受支持。")
            for row in connection.execute("SELECT object_path FROM documents"):
                relative = PurePosixPath(str(row["object_path"]).replace("\\", "/"))
                if _safe_member(relative.as_posix()).parts[0] != "documents":
                    raise BackupValidationError("备份包含越界的文档路径。")
                expected = documents_root.parent.joinpath(*relative.parts)
                if not expected.is_file():
                    raise BackupValidationError(f"备份缺少原始文档：{relative.name}")
        finally:
            connection.close()

    def restore_backup(self, backup_id: str) -> dict[str, Any]:
        if Path(backup_id).name != backup_id:
            raise BackupValidationError("备份标识无效。")
        if self.store.active_job_count() > 0:
            raise RuntimeError("仍有文档处理任务正在运行，请等待任务完成后再恢复。")
        archive_path = self.store.backups_dir / backup_id
        if not archive_path.is_file():
            raise FileNotFoundError(backup_id)

        stage = Path(tempfile.mkdtemp(prefix=".restore-stage-", dir=self.store.data_dir))
        rollback = self.store.data_dir / f".restore-rollback-{uuid4().hex}"
        safety_backup: dict[str, Any] | None = None
        try:
            self._extract_validated(archive_path, stage)
            extracted_database = stage / "knowledge.db"
            extracted_documents = stage / "documents"
            extracted_documents.mkdir(exist_ok=True)
            self._validate_database(extracted_database, extracted_documents)
            safety_backup = self.create_backup(reason="pre-restore")

            rollback.mkdir()
            old_database = rollback / "knowledge.db"
            old_documents = rollback / "documents"
            database_moved = False
            documents_moved = False
            try:
                for suffix in ("-wal", "-shm"):
                    Path(f"{self.store.database_path}{suffix}").unlink(missing_ok=True)
                os.replace(self.store.database_path, old_database)
                database_moved = True
                os.replace(self.store.documents_dir, old_documents)
                documents_moved = True
                os.replace(extracted_database, self.store.database_path)
                os.replace(extracted_documents, self.store.documents_dir)
                self.store._initialize()  # Restored older supported backups are upgraded in place.
                if self.store.schema_status()["integrity"] != "ok":
                    raise BackupValidationError("恢复后的数据库完整性检查失败。")
            except Exception:
                if database_moved and self.store.database_path.exists():
                    self.store.database_path.unlink()
                if documents_moved and self.store.documents_dir.exists():
                    shutil.rmtree(self.store.documents_dir)
                if database_moved and old_database.exists():
                    os.replace(old_database, self.store.database_path)
                if documents_moved and old_documents.exists():
                    os.replace(old_documents, self.store.documents_dir)
                elif not self.store.documents_dir.exists():
                    self.store.documents_dir.mkdir(parents=True)
                raise
            shutil.rmtree(rollback, ignore_errors=True)
            return {
                "restored": True,
                "backup_id": backup_id,
                "safety_backup_id": safety_backup["id"] if safety_backup else None,
                "schema": self.store.schema_status(),
            }
        finally:
            shutil.rmtree(stage, ignore_errors=True)
            if rollback.exists() and not (rollback / "knowledge.db").exists():
                shutil.rmtree(rollback, ignore_errors=True)
