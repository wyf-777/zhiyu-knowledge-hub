import { useCallback, useEffect, useRef, useState } from 'react';
import type { DragEvent } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Database,
  FileSearch,
  FileText,
  LoaderCircle,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from 'lucide-react';
import { knowledgeApiService } from '../services/KnowledgeApiService';
import type { RuntimeStatus } from '../services/RuntimeService';
import type { KnowledgeBase, KnowledgeDocument, RetrievalResult } from '../types';

const statusLabels: Record<KnowledgeDocument['status'], string> = {
  queued: '等待处理',
  processing: '正在索引',
  ready: '可检索',
  failed: '处理失败',
};

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

function StatusIcon({ status }: { status: KnowledgeDocument['status'] }) {
  if (status === 'ready') return <CheckCircle2 size={16} />;
  if (status === 'failed') return <AlertTriangle size={16} />;
  return <LoaderCircle className={status === 'processing' ? 'spin' : undefined} size={16} />;
}

interface DocumentManagementPageProps {
  runtimeStatus: RuntimeStatus;
  knowledgeBases: KnowledgeBase[];
  initialKnowledgeBaseId: string;
  onBack: () => void;
  onSelectKnowledgeBase: (id: string) => void;
  onDataChanged: () => void | Promise<void>;
}

export function DocumentManagementPage({
  runtimeStatus,
  knowledgeBases,
  initialKnowledgeBaseId,
  onBack,
  onSelectKnowledgeBase,
  onDataChanged,
}: DocumentManagementPageProps) {
  const [knowledgeBaseId, setKnowledgeBaseId] = useState(initialKnowledgeBaseId);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<RetrievalResult[]>([]);
  const [searchCompleted, setSearchCompleted] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const runtimeReady = runtimeStatus.phase === 'running' && Boolean(runtimeStatus.baseUrl && runtimeStatus.token);

  const loadDocuments = useCallback(async (quiet = false) => {
    if (!runtimeReady) {
      setLoading(false);
      return;
    }
    if (!quiet) setLoading(true);
    try {
      const next = await knowledgeApiService.listDocuments(runtimeStatus, knowledgeBaseId);
      setDocuments(next);
      setError(null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '无法读取本地文档。');
    } finally {
      setLoading(false);
    }
  }, [knowledgeBaseId, runtimeReady, runtimeStatus]);

  useEffect(() => {
    void loadDocuments();
    if (!runtimeReady) return;
    const timer = window.setInterval(() => void loadDocuments(true), 1800);
    return () => window.clearInterval(timer);
  }, [loadDocuments, runtimeReady]);

  const selectKnowledgeBase = (id: string) => {
    setKnowledgeBaseId(id);
    setResults([]);
    setSearchCompleted(false);
    onSelectKnowledgeBase(id);
  };

  const uploadFiles = async (incoming: File[]) => {
    if (!runtimeReady || incoming.length === 0 || uploading) return;
    setUploading(true);
    setError(null);
    try {
      await knowledgeApiService.uploadDocuments(runtimeStatus, knowledgeBaseId, incoming);
      await loadDocuments(true);
      await onDataChanged();
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : '文档上传失败。');
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    void uploadFiles(Array.from(event.dataTransfer.files));
  };

  const deleteDocument = async (document: KnowledgeDocument) => {
    if (!window.confirm(`确定从本地知识库中删除“${document.sourceName}”吗？原始副本和索引都会被删除。`)) return;
    try {
      await knowledgeApiService.deleteDocument(runtimeStatus, document.id);
      await loadDocuments(true);
      await onDataChanged();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '文档删除失败。');
    }
  };

  const retryDocument = async (document: KnowledgeDocument) => {
    try {
      await knowledgeApiService.retryDocument(runtimeStatus, document.id);
      await loadDocuments(true);
      await onDataChanged();
    } catch (retryError) {
      setError(retryError instanceof Error ? retryError.message : '重新索引失败。');
    }
  };

  const reindexKnowledgeBase = async () => {
    if (!runtimeReady || reindexing || documents.length === 0) return;
    setReindexing(true);
    setError(null);
    try {
      await knowledgeApiService.reindexKnowledgeBase(runtimeStatus, knowledgeBaseId);
      await loadDocuments(true);
      await onDataChanged();
    } catch (reindexError) {
      setError(reindexError instanceof Error ? reindexError.message : '重建索引失败。');
    } finally {
      setReindexing(false);
    }
  };

  const runSearch = async () => {
    const normalized = query.trim();
    if (!normalized || !runtimeReady || searching) return;
    setSearching(true);
    setSearchCompleted(false);
    try {
      setResults(await knowledgeApiService.search(runtimeStatus, knowledgeBaseId, normalized));
      setSearchCompleted(true);
      setError(null);
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : '本地检索失败。');
    } finally {
      setSearching(false);
    }
  };

  const selectedKnowledgeBase = knowledgeBases.find((item) => item.id === knowledgeBaseId);

  return (
    <div className="document-management-page">
      <header className="document-page-topbar" data-tauri-drag-region="">
        <button className="document-back-button" onClick={onBack}><ArrowLeft size={18} />返回对话</button>
        <div><strong>文档管理</strong><span>文件和索引仅保存在本地</span></div>
      </header>

      <div className="document-page-body">
        <nav className="document-kb-tabs" aria-label="选择知识库">
          {knowledgeBases.map((item) => (
            <button key={item.id} className={item.id === knowledgeBaseId ? 'is-active' : ''} onClick={() => selectKnowledgeBase(item.id)}>
              <Database size={16} /><span>{item.name}</span><small>{item.documentCount}</small>
            </button>
          ))}
        </nav>

        {!runtimeReady && (
          <div className="document-inline-error" role="alert"><AlertTriangle size={18} /><div><strong>本地服务尚未连接</strong><span>{runtimeStatus.message}</span></div></div>
        )}
        {error && <div className="document-inline-error" role="alert"><AlertTriangle size={18} /><div><strong>操作未完成</strong><span>{error}</span></div><button onClick={() => void loadDocuments()}><RefreshCw size={15} />重试</button></div>}

        <div className="document-workspace-grid">
          <main className="document-library-pane">
            <div className="document-pane-heading">
              <div className="document-pane-copy"><h1>{selectedKnowledgeBase?.name ?? '知识库'}</h1><p>支持 PDF（含本地 OCR）、DOCX、XLS/XLSX、PPTX、HTML、TXT 和 Markdown，单个文件不超过 50 MB。</p></div>
              <div className="document-heading-actions">
                <button className="document-upload-button is-secondary" disabled={!runtimeReady || reindexing || documents.length === 0} onClick={() => void reindexKnowledgeBase()}>
                  {reindexing ? <LoaderCircle className="spin" size={17} /> : <RefreshCw size={17} />}{reindexing ? '正在提交' : '重建索引'}
                </button>
                <button className="document-upload-button" disabled={!runtimeReady || uploading} onClick={() => inputRef.current?.click()}>
                  {uploading ? <LoaderCircle className="spin" size={17} /> : <Upload size={17} />}{uploading ? '正在导入' : '导入文档'}
                </button>
              </div>
              <input
                ref={inputRef}
                type="file"
                hidden
                multiple
                accept=".pdf,.docx,.xls,.xlsx,.pptx,.html,.htm,.txt,.md,.markdown"
                onChange={(event) => void uploadFiles(Array.from(event.target.files ?? []))}
              />
            </div>

            <div
              className={`document-dropzone ${dragging ? 'is-dragging' : ''}`}
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
              onDrop={handleDrop}
            >
              <Upload size={19} /><span>将文档拖到这里，系统会自动解析、切分并建立本地索引</span>
            </div>

            <section className="document-list-section" aria-live="polite">
              <div className="document-list-heading"><span>全部文档</span><small>{documents.length} 项</small></div>
              {loading && <div className="document-empty"><LoaderCircle className="spin" size={22} /><span>正在读取本地文档…</span></div>}
              {!loading && documents.length === 0 && <div className="document-empty"><FileText size={23} /><strong>这个知识库还没有文档</strong><span>导入第一份资料后即可进行本地检索。</span></div>}
              {!loading && documents.map((document) => (
                <article className="document-row" key={document.id}>
                  <span className="document-format-icon"><FileText size={19} /></span>
                  <div className="document-row-main">
                    <strong title={document.sourceName}>{document.sourceName}</strong>
                    <span>{document.sourceType.toUpperCase()}　·　{formatBytes(document.sizeBytes)}　·　{document.chunkCount} 个文本块　·　{formatTime(document.updatedAt)}</span>
                    {document.errorMessage && <small>{document.errorMessage}</small>}
                  </div>
                  <span className={`document-status status-${document.status}`}><StatusIcon status={document.status} />{statusLabels[document.status]}</span>
                  <div className="document-row-actions">
                    {document.status === 'failed' && <button onClick={() => void retryDocument(document)} aria-label={`重新索引 ${document.sourceName}`}><RefreshCw size={16} /></button>}
                    <button onClick={() => void deleteDocument(document)} aria-label={`删除 ${document.sourceName}`}><Trash2 size={16} /></button>
                  </div>
                </article>
              ))}
            </section>
          </main>

          <aside className="retrieval-test-pane">
            <div className="retrieval-heading"><FileSearch size={19} /><div><strong>检索测试</strong><span>直接查看切分和召回效果</span></div></div>
            <div className="retrieval-search-box">
              <textarea value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`在“${selectedKnowledgeBase?.name ?? '知识库'}”中测试检索…`} />
              <button disabled={!query.trim() || searching || !runtimeReady} onClick={() => void runSearch()}>{searching ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}检索</button>
            </div>
            <p className="retrieval-index-note">当前使用轻量本地向量索引，不会下载模型或上传文档。</p>
            <div className="retrieval-results">
              {!searchCompleted && !searching && <div className="retrieval-empty"><Search size={21} /><span>输入问题，检查系统实际召回的原文片段。</span></div>}
              {searchCompleted && results.length === 0 && <div className="retrieval-empty"><FileSearch size={21} /><span>没有找到相关内容，请尝试更具体的关键词。</span></div>}
              {results.map((result, index) => (
                <article className="retrieval-result" key={result.chunkId}>
                  <div><span>{index + 1}</span><strong title={result.documentName}>{result.documentName}</strong><small>{Math.round(result.score * 100)}%</small></div>
                  <p>{result.page ? `第 ${result.page} 页　·　` : ''}{result.heading || result.format}</p>
                  <blockquote>{result.content}</blockquote>
                </article>
              ))}
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
