import { FileSearch, FileText, LoaderCircle, Search, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { knowledgeApiService } from '../services/KnowledgeApiService';
import type { RuntimeStatus } from '../services/RuntimeService';
import type { ChatMessage, Conversation, KnowledgeBase, RetrievalResult } from '../types';

interface KnowledgeSearchDialogProps {
  open: boolean;
  runtimeStatus: RuntimeStatus;
  knowledgeBase: KnowledgeBase;
  conversations: Conversation[];
  messages: ChatMessage[];
  onClose: () => void;
  onSelectConversation: (id: string) => void;
}

interface LocalResult { id: string; title: string; excerpt: string; conversationId?: string }

export function KnowledgeSearchDialog({ open, runtimeStatus, knowledgeBase, conversations, messages, onClose, onSelectConversation }: KnowledgeSearchDialogProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [retrievalResults, setRetrievalResults] = useState<RetrievalResult[]>([]);
  const [localResults, setLocalResults] = useState<LocalResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => inputRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  useEffect(() => {
    setQuery('');
    setRetrievalResults([]);
    setLocalResults([]);
    setSearched(false);
    setError('');
  }, [knowledgeBase.id]);

  const search = async () => {
    const normalized = query.trim();
    if (!normalized || searching) return;
    setSearching(true);
    setSearched(true);
    setError('');
    setRetrievalResults([]);
    setLocalResults([]);
    try {
      if (runtimeStatus.phase === 'running') {
        setRetrievalResults(await knowledgeApiService.search(runtimeStatus, knowledgeBase.id, normalized));
        const needle = normalized.toLocaleLowerCase();
        const conversationMatches = conversations.filter((item) => item.title.toLocaleLowerCase().includes(needle)).map((item) => ({ id: `conversation-${item.id}`, title: item.title, excerpt: '最近对话', conversationId: item.id }));
        setLocalResults(conversationMatches);
      } else {
        const needle = normalized.toLocaleLowerCase();
        const conversationMatches = conversations.filter((item) => item.title.toLocaleLowerCase().includes(needle)).map((item) => ({ id: `conversation-${item.id}`, title: item.title, excerpt: '最近对话', conversationId: item.id }));
        const messageMatches = messages.filter((item) => item.content.toLocaleLowerCase().includes(needle)).map((item) => ({ id: item.id, title: item.role === 'user' ? '你的问题' : '知识助手回答', excerpt: item.content.slice(0, 240) }));
        setLocalResults([...conversationMatches, ...messageMatches]);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '搜索失败');
    } finally {
      setSearching(false);
    }
  };

  if (!open) return null;
  const resultCount = retrievalResults.length + localResults.length;
  return (
    <div className="search-dialog-layer" role="presentation">
      <button className="search-dialog-scrim" aria-label="关闭搜索" onClick={onClose} />
      <section className="knowledge-search-dialog glass" role="dialog" aria-modal="true" aria-label="搜索知识库">
        <header><Search size={19} /><strong>搜索“{knowledgeBase.name}”</strong><button className="icon-button compact" onClick={onClose} aria-label="关闭搜索"><X size={17} /></button></header>
        <form onSubmit={(event) => { event.preventDefault(); void search(); }}>
          <input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索文档内容或最近对话…" aria-label="搜索内容" />
          <button disabled={!query.trim() || searching}>{searching ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}搜索</button>
        </form>
        <div className="knowledge-search-results">
          {!searched && <div className="search-dialog-empty"><FileSearch size={22} /><span>桌面版会搜索当前知识库的真实索引。</span></div>}
          {searched && !searching && !error && resultCount === 0 && <div className="search-dialog-empty"><FileSearch size={22} /><span>没有找到相关内容。</span></div>}
          {error && <div className="search-dialog-empty"><FileSearch size={22} /><span>{error}</span></div>}
          {retrievalResults.map((result) => <article className="knowledge-search-result" key={result.chunkId}><div><FileText size={17} /><strong>{result.documentName}</strong><small>{Math.round(result.score * 100)}%</small></div><span>{result.heading || result.format}{result.page ? ` · 第 ${result.page} 页` : ''}</span><p>{result.content}</p></article>)}
          {localResults.map((result) => result.conversationId ? <button className="knowledge-search-result is-button" key={result.id} onClick={() => { onSelectConversation(result.conversationId!); onClose(); }}><div><FileText size={17} /><strong>{result.title}</strong></div><span>{result.excerpt}</span></button> : <article className="knowledge-search-result" key={result.id}><div><FileText size={17} /><strong>{result.title}</strong></div><p>{result.excerpt}</p></article>)}
        </div>
      </section>
    </div>
  );
}
