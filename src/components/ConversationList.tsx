import { FileText, ListFilter, MoreHorizontal, Pencil, Pin, Search, Trash2, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { Conversation } from '../types';

interface ConversationListProps {
  items: Conversation[];
  selectedId: string | null;
  collapsed: boolean;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onPin: (id: string) => void;
  onDelete: (id: string) => void;
}

export function ConversationList({ items, selectedId, collapsed, onSelect, onRename, onPin, onDelete }: ConversationListProps) {
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [pinnedOnly, setPinnedOnly] = useState(false);

  useEffect(() => {
    if (!openMenuId) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      const target = event.target instanceof Element ? event.target.closest('[data-conversation-id]') : null;
      if (target?.getAttribute('data-conversation-id') !== openMenuId) setOpenMenuId(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpenMenuId(null);
    };
    document.addEventListener('pointerdown', closeOnOutsideClick);
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideClick);
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [openMenuId]);

  if (collapsed) return null;
  const normalizedFilter = filterText.trim().toLocaleLowerCase();
  const visibleItems = items.filter((item) => (!pinnedOnly || item.pinned) && (!normalizedFilter || item.title.toLocaleLowerCase().includes(normalizedFilter)));

  return (
    <div className="sidebar-section conversation-section">
      <div className="sidebar-title-row">
        <p className="sidebar-section-title">最近对话</p>
        <button className={`icon-button compact ${filterOpen || filterText || pinnedOnly ? 'is-selected' : ''}`} aria-label="筛选最近对话" onClick={() => setFilterOpen((value) => !value)} aria-expanded={filterOpen}>
          <ListFilter size={16} />
        </button>
        {filterOpen && <div className="conversation-filter glass">
          <div><Search size={14} /><input value={filterText} onChange={(event) => setFilterText(event.target.value)} placeholder="筛选标题…" autoFocus /><button onClick={() => { setFilterText(''); setPinnedOnly(false); }} aria-label="清除筛选"><X size={13} /></button></div>
          <label><input type="checkbox" checked={pinnedOnly} onChange={(event) => setPinnedOnly(event.target.checked)} />仅显示置顶对话</label>
        </div>}
      </div>
      <div className="conversation-list">
        {visibleItems.map((item) => (
          <div className={`conversation-item ${selectedId === item.id ? 'is-active' : ''}`} data-conversation-id={item.id} key={item.id}>
            {renamingId === item.id ? (
              <input
                className="conversation-rename-input"
                value={renameValue}
                autoFocus
                onChange={(event) => setRenameValue(event.target.value)}
                onBlur={() => {
                  if (renameValue.trim()) onRename(item.id, renameValue.trim());
                  setRenamingId(null);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') event.currentTarget.blur();
                  if (event.key === 'Escape') setRenamingId(null);
                }}
              />
            ) : (
              <button className="conversation-row" onClick={() => onSelect(item.id)} title={item.title}>
                <FileText size={17} strokeWidth={1.7} />
                <span>{item.title}</span>
                {item.pinned && <Pin size={12} className="pin-indicator" />}
              </button>
            )}
            <button
              className="conversation-more"
              aria-label={`管理对话：${item.title}`}
              onClick={() => setOpenMenuId((value) => (value === item.id ? null : item.id))}
            >
              <MoreHorizontal size={15} />
            </button>
            {openMenuId === item.id && (
              <div className="conversation-menu glass">
                <button onClick={() => { setRenamingId(item.id); setRenameValue(item.title); setOpenMenuId(null); }}><Pencil size={14} />重命名</button>
                <button onClick={() => { onPin(item.id); setOpenMenuId(null); }}><Pin size={14} />{item.pinned ? '取消置顶' : '置顶'}</button>
                <button onClick={() => { onDelete(item.id); setOpenMenuId(null); }}><Trash2 size={14} />删除</button>
              </div>
            )}
          </div>
        ))}
        {visibleItems.length === 0 && <p className="conversation-filter-empty">没有匹配的对话</p>}
      </div>
    </div>
  );
}
