import { AtSign, Check, ChevronDown, FileText, LoaderCircle, Paperclip, Send, Square } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { KnowledgeDocument } from '../types';

interface ChatComposerProps {
  value: string;
  knowledgeBaseName: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  isGenerating: boolean;
  textareaRef: RefObject<HTMLTextAreaElement>;
  documents: KnowledgeDocument[];
  selectedDocumentIds: string[];
  canManageDocuments: boolean;
  attachmentUploading: boolean;
  attachmentStatus: string;
  onSelectedDocumentsChange: (ids: string[]) => void;
  onAttachFiles: (files: File[]) => void;
}

export function ChatComposer({ value, knowledgeBaseName, onChange, onSend, onStop, isGenerating, textareaRef, documents, selectedDocumentIds, canManageDocuments, attachmentUploading, attachmentStatus, onSelectedDocumentsChange, onAttachFiles }: ChatComposerProps) {
  const [documentMenuOpen, setDocumentMenuOpen] = useState(false);
  const toolsRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 42), 150)}px`;
  }, [textareaRef, value]);

  useEffect(() => {
    if (!documentMenuOpen) return;
    const close = (event: PointerEvent) => { if (event.target instanceof Node && !toolsRef.current?.contains(event.target)) setDocumentMenuOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setDocumentMenuOpen(false); };
    document.addEventListener('pointerdown', close);
    window.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', close); window.removeEventListener('keydown', escape); };
  }, [documentMenuOpen]);

  const readyDocuments = documents.filter((document) => document.status === 'ready');
  const toggleDocument = (id: string) => onSelectedDocumentsChange(selectedDocumentIds.includes(id) ? selectedDocumentIds.filter((item) => item !== id) : [...selectedDocumentIds, id]);

  return (
    <div className="composer-shell">
      <div className="composer glass">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={`向“${knowledgeBaseName}”提问，输入 @ 可指定文档...`}
          rows={1}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              if (!isGenerating) onSend();
            }
          }}
        />
        <div className="composer-toolbar">
          <div className="composer-tools" ref={toolsRef}>
            <button className="composer-tool" onClick={() => fileInputRef.current?.click()} disabled={!canManageDocuments || attachmentUploading} title={canManageDocuments ? '上传并索引文档' : '请在桌面版中上传文档'}>{attachmentUploading ? <LoaderCircle className="spin" size={17} /> : <Paperclip size={17} />}{attachmentUploading ? '正在上传' : '附件'}</button>
            <input ref={fileInputRef} type="file" hidden multiple accept=".pdf,.docx,.xlsx,.pptx,.html,.htm,.txt,.md,.markdown" onChange={(event) => { const files = Array.from(event.target.files ?? []); if (files.length) onAttachFiles(files); event.currentTarget.value = ''; }} />
            <button className={`composer-tool ${selectedDocumentIds.length ? 'is-selected' : ''}`} onClick={() => setDocumentMenuOpen((value) => !value)} disabled={!canManageDocuments} aria-expanded={documentMenuOpen}><AtSign size={17} />{selectedDocumentIds.length ? `已指定 ${selectedDocumentIds.length} 份` : '指定文档'}<ChevronDown size={14} /></button>
            {documentMenuOpen && <div className="composer-document-menu glass">
              <div><strong>限定问答范围</strong><span>只从选中的文档检索</span></div>
              <button className={selectedDocumentIds.length === 0 ? 'is-active' : ''} onClick={() => onSelectedDocumentsChange([])}><span className="document-check">{selectedDocumentIds.length === 0 && <Check size={13} />}</span><span>全部文档</span></button>
              {readyDocuments.map((document) => <button key={document.id} className={selectedDocumentIds.includes(document.id) ? 'is-active' : ''} onClick={() => toggleDocument(document.id)}><span className="document-check">{selectedDocumentIds.includes(document.id) && <Check size={13} />}</span><FileText size={15} /><span title={document.sourceName}>{document.sourceName}</span></button>)}
              {readyDocuments.length === 0 && <p>当前知识库没有可检索文档</p>}
            </div>}
            {attachmentStatus && <span className="composer-attachment-status" role="status">{attachmentStatus}</span>}
          </div>
          <div className="composer-send-area">
            <span>Enter 发送，Shift + Enter 换行</span>
            <button
              className="send-button"
              onClick={isGenerating ? onStop : onSend}
              aria-label={isGenerating ? '停止生成' : '发送问题'}
              disabled={!isGenerating && !value.trim()}
            >
              {isGenerating ? <Square size={17} fill="currentColor" /> : <Send size={19} />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
