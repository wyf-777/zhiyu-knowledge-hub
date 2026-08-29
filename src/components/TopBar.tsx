import { BookOpen, ChevronDown, FileStack, Menu, MessageSquarePlus, MoreVertical, PanelLeftOpen, Search, Settings } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { RuntimeStatus } from '../services/RuntimeService';
import type { KnowledgeBase } from '../types';
import { LocalRuntimePopover } from './LocalRuntimePopover';

interface TopBarProps {
  knowledgeBase: KnowledgeBase;
  knowledgeBases: KnowledgeBase[];
  runtimeStatus: RuntimeStatus;
  runtimeOpen: boolean;
  sidebarCollapsed: boolean;
  onRuntimeToggle: () => void;
  onRuntimeRefresh: () => void;
  onOpenMobileSidebar: () => void;
  onExpandSidebar: () => void;
  onCitationToggle: () => void;
  onSelectKnowledgeBase: (id: string) => void;
  onSearch: () => void;
  onNewConversation: () => void;
  onOpenDocuments: () => void;
  onOpenSettings: () => void;
}

export function TopBar({
  knowledgeBase,
  knowledgeBases,
  runtimeStatus,
  runtimeOpen,
  sidebarCollapsed,
  onRuntimeToggle,
  onRuntimeRefresh,
  onOpenMobileSidebar,
  onExpandSidebar,
  onCitationToggle,
  onSelectKnowledgeBase,
  onSearch,
  onNewConversation,
  onOpenDocuments,
  onOpenSettings,
}: TopBarProps) {
  const [knowledgeMenuOpen, setKnowledgeMenuOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const menusRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!knowledgeMenuOpen && !moreOpen) return;
    const close = (event: PointerEvent) => {
      if (event.target instanceof Node && !menusRef.current?.contains(event.target)) {
        setKnowledgeMenuOpen(false);
        setMoreOpen(false);
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setKnowledgeMenuOpen(false); setMoreOpen(false); }
    };
    document.addEventListener('pointerdown', close);
    window.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', close); window.removeEventListener('keydown', escape); };
  }, [knowledgeMenuOpen, moreOpen]);

  return (
    <header className="top-bar glass" data-tauri-drag-region="" ref={menusRef}>
      <div className="top-bar-left">
        <button className="icon-button mobile-menu-button" data-tooltip="打开导航" onClick={onOpenMobileSidebar} aria-label="打开导航"><Menu size={20} /></button>
        {sidebarCollapsed && <button className="icon-button desktop-sidebar-expand" data-tooltip="展开侧边栏" onClick={onExpandSidebar} aria-label="展开侧边栏"><PanelLeftOpen size={19} /></button>}
        <button className="knowledge-title-button" onClick={() => { setKnowledgeMenuOpen((value) => !value); setMoreOpen(false); }} aria-expanded={knowledgeMenuOpen}>
          <strong>{knowledgeBase.name}</strong><ChevronDown size={16} />
        </button>
        {knowledgeMenuOpen && <div className="top-knowledge-menu glass">{knowledgeBases.map((item) => <button key={item.id} className={item.id === knowledgeBase.id ? 'is-active' : ''} onClick={() => { onSelectKnowledgeBase(item.id); setKnowledgeMenuOpen(false); }}><span>{item.name}</span><small>{item.documentCount} 份文档</small></button>)}</div>}
        <span className="top-meta">{knowledgeBase.documentCount} 份文档</span>
        <span className="meta-dot">•</span>
        <span className="top-meta">索引已更新</span>
        <span className="meta-dot">•</span>
        <span className="top-meta">{knowledgeBase.updatedLabel}</span>
      </div>
      <div className="top-bar-actions">
        <LocalRuntimePopover open={runtimeOpen} status={runtimeStatus} onToggle={onRuntimeToggle} onRefresh={onRuntimeRefresh} />
        <button className="round-action" data-tooltip="搜索" aria-label="搜索" onClick={onSearch}><Search size={19} /></button>
        <button className="round-action" data-tooltip="回答依据" aria-label="回答依据" onClick={onCitationToggle}><BookOpen size={19} /></button>
        <div className="top-more-anchor">
          <button className="round-action" data-tooltip="更多" aria-label="更多" onClick={() => { setMoreOpen((value) => !value); setKnowledgeMenuOpen(false); }} aria-expanded={moreOpen}><MoreVertical size={19} /></button>
          {moreOpen && <div className="top-more-menu glass">
            <button onClick={() => { onNewConversation(); setMoreOpen(false); }}><MessageSquarePlus size={16} />新建对话</button>
            <button onClick={() => { onOpenDocuments(); setMoreOpen(false); }}><FileStack size={16} />文档管理</button>
            <button onClick={() => { onOpenSettings(); setMoreOpen(false); }}><Settings size={16} />设置</button>
          </div>}
        </div>
      </div>
    </header>
  );
}
