import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Archive, CheckCircle2, FolderOpen, HardDrive, LoaderCircle, RotateCcw, Upload } from 'lucide-react';
import { knowledgeApiService } from '../services/KnowledgeApiService';
import type { LocalBackup, StorageStatus } from '../services/KnowledgeApiService';
import type { RuntimeStatus } from '../services/RuntimeService';

interface StorageSettingsPanelProps {
  runtimeStatus: RuntimeStatus;
  onDataRestored: () => void;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

export function StorageSettingsPanel({ runtimeStatus, onDataRestored }: StorageSettingsPanelProps) {
  const [status, setStatus] = useState<StorageStatus | null>(null);
  const [backups, setBackups] = useState<LocalBackup[]>([]);
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const connected = runtimeStatus.phase === 'running';

  const refresh = useCallback(async () => {
    if (!connected) return;
    try {
      const [nextStatus, nextBackups] = await Promise.all([
        knowledgeApiService.getStorageStatus(runtimeStatus),
        knowledgeApiService.listBackups(runtimeStatus),
      ]);
      setStatus(nextStatus);
      setBackups(nextBackups);
      setError('');
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '无法读取本地存储状态');
    }
  }, [connected, runtimeStatus.baseUrl, runtimeStatus.token]);

  useEffect(() => { void refresh(); }, [refresh]);

  const createBackup = async () => {
    setBusy('create');
    setMessage('');
    setError('');
    try {
      const backup = await knowledgeApiService.createBackup(runtimeStatus);
      setMessage(`备份已创建并通过校验：${backup.id}`);
      await refresh();
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : '创建备份失败');
    } finally {
      setBusy('');
    }
  };

  const importBackup = async (file: File) => {
    setBusy('import');
    setMessage('');
    setError('');
    try {
      const backup = await knowledgeApiService.importBackup(runtimeStatus, file);
      setMessage(`备份已导入并通过完整性校验：${backup.id}`);
      await refresh();
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : '导入备份失败');
    } finally {
      setBusy('');
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const restoreBackup = async (backup: LocalBackup) => {
    if (backup.state !== 'valid') return;
    const confirmed = window.confirm(
      `确定恢复 ${formatDate(backup.createdAt)} 的备份吗？\n\n当前数据会先自动创建安全备份，校验或恢复失败时不会覆盖现有数据。`,
    );
    if (!confirmed) return;
    setBusy(backup.id);
    setMessage('');
    setError('');
    try {
      const result = await knowledgeApiService.restoreBackup(runtimeStatus, backup.id);
      setMessage(`恢复完成。恢复前安全备份：${result.safety_backup_id ?? '已创建'}`);
      window.setTimeout(onDataRestored, 700);
    } catch (restoreError) {
      setError(restoreError instanceof Error ? restoreError.message : '恢复备份失败，原数据未改变');
    } finally {
      setBusy('');
    }
  };

  const openFolder = async () => {
    try {
      await knowledgeApiService.openBackupFolder(runtimeStatus);
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : '无法打开备份目录');
    }
  };

  return (
    <>
      <section className="settings-section" aria-labelledby="storage-status-title">
        <div className="settings-section-heading">
          <h2 id="storage-status-title">数据完整性</h2>
          <p>知识库、索引、后台任务、会话和消息统一保存在本机 SQLite；启动升级前会自动创建数据库快照。</p>
        </div>
        <div className="storage-status-card" aria-disabled={!connected}>
          <div className="storage-health-icon">
            {status?.integrity === 'ok' ? <CheckCircle2 size={20} /> : <HardDrive size={20} />}
          </div>
          <div>
            <strong>{!connected ? '本地服务未连接' : status?.integrity === 'ok' ? '数据库完整性检查通过' : '正在检查本地数据'}</strong>
            <span>{status ? `结构版本 ${status.schemaVersion}/${status.currentSchemaVersion} · ${status.conversationCount} 个已持久化对话` : runtimeStatus.message}</span>
          </div>
        </div>
        {status && <div className="storage-paths">
          <div><span>数据目录</span><code>{status.dataDirectory}</code></div>
          <div><span>备份目录</span><code>{status.backupDirectory}</code></div>
        </div>}
      </section>

      <section className="settings-section" aria-labelledby="backup-settings-title">
        <div className="settings-section-heading storage-heading-row">
          <div><h2 id="backup-settings-title">备份与恢复</h2><p>备份包含数据库及已导入的原始文档。恢复前会校验格式、版本、哈希和数据库完整性。</p></div>
          <div className="storage-actions">
            <input ref={inputRef} type="file" accept=".zhiyu-backup" hidden onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void importBackup(file);
            }} />
            <button onClick={() => inputRef.current?.click()} disabled={!connected || Boolean(busy)}><Upload size={15} />导入备份</button>
            <button onClick={() => void createBackup()} disabled={!connected || Boolean(busy)} className="is-primary">
              {busy === 'create' ? <LoaderCircle className="is-spinning" size={15} /> : <Archive size={15} />}创建备份
            </button>
          </div>
        </div>

        {(error || message) && <div className={`storage-message ${error ? 'is-error' : ''}`} role="status">
          {error ? <AlertTriangle size={16} /> : <CheckCircle2 size={16} />}
          <span>{error || message}</span>
        </div>}

        <div className="backup-list">
          {backups.map((backup) => (
            <article key={backup.id} className="backup-row">
              <div className="backup-icon">{backup.state === 'valid' ? <Archive size={18} /> : <AlertTriangle size={18} />}</div>
              <div className="backup-details">
                <strong>{formatDate(backup.createdAt)}</strong>
                <span>{formatBytes(backup.sizeBytes)} · {backup.documentFileCount} 个原始文档 · 数据结构 v{backup.schemaVersion ?? '?'}</span>
                <small>{backup.state === 'valid' ? backup.id : `不可恢复：${backup.message}`}</small>
              </div>
              <button
                className="restore-backup-button"
                disabled={backup.state !== 'valid' || Boolean(busy)}
                onClick={() => void restoreBackup(backup)}
              >
                {busy === backup.id ? <LoaderCircle className="is-spinning" size={14} /> : <RotateCcw size={14} />}恢复
              </button>
            </article>
          ))}
          {connected && backups.length === 0 && <div className="backup-empty"><Archive size={21} /><span>还没有本地备份</span></div>}
          {!connected && <div className="backup-empty"><AlertTriangle size={21} /><span>连接本地服务后才能管理备份</span></div>}
        </div>
        <button className="open-backup-folder" onClick={() => void openFolder()} disabled={!connected}><FolderOpen size={15} />打开备份文件夹</button>
      </section>
    </>
  );
}
