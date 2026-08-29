import { Box, ChevronsLeft, ChevronsRight, FileStack, Plus, Settings, UserRound } from 'lucide-react';
import type { Conversation, KnowledgeBase } from '../types';
import { ConversationList } from './ConversationList';
import { KnowledgeBaseList } from './KnowledgeBaseList';

interface SidebarProps {
  knowledgeBases: KnowledgeBase[];
  conversations: Conversation[];
  selectedKnowledgeBaseId: string;
  selectedConversationId: string | null;
  collapsed: boolean;
  mobileOpen: boolean;
  onToggle: () => void;
  onCloseMobile: () => void;
  onNewConversation: () => void;
  onSelectKnowledgeBase: (id: string) => void;
  onSelectConversation: (id: string) => void;
  onRenameConversation: (id: string, title: string) => void;
  onPinConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onOpenSettings: () => void;
  onOpenDocumentManager: () => void;
}

export function Sidebar({
  knowledgeBases,
  conversations,
  selectedKnowledgeBaseId,
  selectedConversationId,
  collapsed,
  mobileOpen,
  onToggle,
  onCloseMobile,
  onNewConversation,
  onSelectKnowledgeBase,
  onSelectConversation,
  onRenameConversation,
  onPinConversation,
  onDeleteConversation,
  onOpenSettings,
  onOpenDocumentManager,
}: SidebarProps) {
  return (
    <>
      <aside
        className={`sidebar glass ${collapsed ? 'is-collapsed' : ''} ${mobileOpen ? 'is-mobile-open' : ''}`}
        aria-hidden={collapsed && !mobileOpen}
      >
        <div className="brand-row" data-tauri-drag-region="">
          <div className="brand-mark"><Box size={24} strokeWidth={1.6} /></div>
          {!collapsed && <span className="brand-name">知域</span>}
          <button className="icon-button sidebar-toggle" onClick={onToggle} aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}>
            {collapsed ? <ChevronsRight size={19} /> : <ChevronsLeft size={19} />}
          </button>
        </div>

        <button className="new-chat-button" onClick={onNewConversation}>
          <Plus size={19} />
          {!collapsed && <span>新建对话</span>}
        </button>

        <div className="sidebar-scroll">
          <KnowledgeBaseList
            items={knowledgeBases}
            selectedId={selectedKnowledgeBaseId}
            collapsed={collapsed}
            onSelect={onSelectKnowledgeBase}
          />
          <ConversationList
            items={conversations}
            selectedId={selectedConversationId}
            collapsed={collapsed}
            onSelect={onSelectConversation}
            onRename={onRenameConversation}
            onPin={onPinConversation}
            onDelete={onDeleteConversation}
          />
        </div>

        <nav className="sidebar-footer-nav">
          <button className="footer-nav-row" title="文档管理" onClick={onOpenDocumentManager}>
            <FileStack size={19} /> {!collapsed && <span>文档管理</span>}
          </button>
          <button className="footer-nav-row" title="设置" onClick={onOpenSettings}>
            <Settings size={19} /> {!collapsed && <span>设置</span>}
          </button>
        </nav>
        <div className="account-row">
          <span className="avatar"><UserRound size={17} /></span>
          {!collapsed && <span className="account-name">user_demo</span>}
        </div>
      </aside>
      {mobileOpen && <button className="mobile-scrim" aria-label="关闭侧边栏" onClick={onCloseMobile} />}
    </>
  );
}
