import { ExternalLink, FileSearch, FileText, History, LoaderCircle, ScanSearch, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { Citation } from '../types';

interface CitationDrawerProps {
  open: boolean;
  items: Citation[];
  activeId: number | null;
  loading: boolean;
  onClose: () => void;
  onSelect: (id: number) => void;
  onOpenDocument: (citation: Citation) => Promise<void>;
  onPreviewDocument: (citation: Citation) => Promise<Blob>;
}

export function CitationDrawer({ open, items, activeId, loading, onClose, onSelect, onOpenDocument, onPreviewDocument }: CitationDrawerProps) {
  const [expandedIds, setExpandedIds] = useState<number[]>([]);
  const [openingId, setOpeningId] = useState<number | null>(null);
  const [actionError, setActionError] = useState('');
  const [preview, setPreview] = useState<{ id: number; url: string; name: string } | null>(null);
  const previewUrlRef = useRef<string | null>(null);

  const openOriginal = async (citation: Citation) => {
    if (!citation.documentId || openingId !== null) return;
    setOpeningId(citation.id);
    setActionError('');
    try { await onOpenDocument(citation); }
    catch (error) { setActionError(error instanceof Error ? error.message : '无法打开本地原文'); }
    finally { setOpeningId(null); }
  };
  const closePreview = useCallback(() => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = null;
    setPreview(null);
  }, []);
  const openPreview = async (citation: Citation) => {
    if (!citation.documentId || !citation.chunkId || openingId !== null) return;
    setOpeningId(citation.id);
    setActionError('');
    try {
      const blob = await onPreviewDocument(citation);
      closePreview();
      const url = URL.createObjectURL(blob);
      previewUrlRef.current = url;
      setPreview({ id: citation.id, url, name: citation.documentName });
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '无法生成 PDF 定位预览');
    } finally {
      setOpeningId(null);
    }
  };
  useEffect(() => () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
  }, []);
  useEffect(() => {
    if (!open) closePreview();
  }, [open, closePreview]);
  return (
    <>
      {open && <button className="citation-scrim" aria-label="点击遮罩关闭回答依据" onClick={onClose} />}
      <aside className={`citation-drawer glass ${open ? 'is-open' : ''}`} aria-hidden={!open}>
        <header className="drawer-heading" data-tauri-drag-region="">
          <div><h2>回答依据</h2><span>{items.length}</span></div>
          <button className="icon-button" onClick={onClose} aria-label="关闭回答依据"><X size={20} /></button>
        </header>
        <div className="citation-list">
          {loading && <div className="drawer-state"><LoaderCircle className="spin" size={22} /><strong>正在加载回答依据</strong><span>正在读取来源上下文…</span></div>}
          {!loading && items.length === 0 && <div className="drawer-state"><FileSearch size={23} /><strong>没有可用来源</strong><span>当前回答没有找到知识库依据。</span></div>}
          {!loading && items.map((citation) => (
            <article
              id={`citation-${citation.id}`}
              key={citation.id}
              className={`citation-card ${activeId === citation.id ? 'is-active' : ''}`}
              tabIndex={0}
              aria-label={`来源 ${citation.id}：${citation.documentName}`}
              onClick={() => onSelect(citation.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onSelect(citation.id);
                }
              }}
            >
              <span className="citation-index">{citation.id}</span>
              <div className="citation-card-main">
                <div className="citation-file-row">
                  <FileText size={25} />
                  <strong title={citation.documentName}>{citation.documentName}</strong>
                </div>
                <p className="citation-meta">{citation.format}　·　{citation.page}　·　{citation.chapter}</p>
                <p className="citation-meta">更新于：{citation.updatedAt}</p>
                <blockquote>{citation.excerpt}</blockquote>
                {expandedIds.includes(citation.id) && <div className="citation-context"><strong>命中片段上下文</strong><p>{citation.context}</p></div>}
                <div className="citation-card-actions">
                  <button onClick={(event) => { event.stopPropagation(); setExpandedIds((current) => current.includes(citation.id) ? current.filter((id) => id !== citation.id) : [...current, citation.id]); }}><History size={15} />{expandedIds.includes(citation.id) ? '收起上下文' : '查看上下文'}</button>
                  {citation.format === 'PDF' && citation.location && <button disabled={openingId === citation.id} onClick={(event) => { event.stopPropagation(); void openPreview(citation); }}><ScanSearch size={15} />定位预览</button>}
                  <button disabled={!citation.documentId || openingId === citation.id} title={citation.documentId ? '使用系统默认应用打开' : '浏览器预览不包含本地文件'} onClick={(event) => { event.stopPropagation(); void openOriginal(citation); }}>{openingId === citation.id ? <LoaderCircle className="spin" size={15} /> : <>在原文中打开<ExternalLink size={15} /></>}</button>
                </div>
              </div>
            </article>
          ))}
          {actionError && <p className="citation-action-error" role="alert">{actionError}</p>}
        </div>
        <p className="citation-disclaimer">以上内容由知识库检索生成，仅供参考，<br />请以原文档为准。</p>
      </aside>
      {preview && (
        <div className="citation-preview-overlay" role="dialog" aria-modal="true" aria-label={`${preview.name} 定位预览`} onClick={closePreview}>
          <div className="citation-preview-panel" onClick={(event) => event.stopPropagation()}>
            <header><div><strong>PDF 定位预览</strong><span>{preview.name}</span></div><button className="icon-button" onClick={closePreview} aria-label="关闭定位预览"><X size={19} /></button></header>
            <div><img src={preview.url} alt={`${preview.name} 的引用位置高亮预览`} /></div>
          </div>
        </div>
      )}
    </>
  );
}
