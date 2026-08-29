import { Database } from 'lucide-react';
import type { KnowledgeBase } from '../types';

interface KnowledgeBaseListProps {
  items: KnowledgeBase[];
  selectedId: string;
  collapsed: boolean;
  onSelect: (id: string) => void;
}

export function KnowledgeBaseList({ items, selectedId, collapsed, onSelect }: KnowledgeBaseListProps) {
  return (
    <div className="sidebar-section">
      {!collapsed && <p className="sidebar-section-title">知识空间</p>}
      <div className="nav-list">
        {items.map((item) => (
          <button
            className={`nav-row ${selectedId === item.id ? 'is-active' : ''}`}
            key={item.id}
            onClick={() => onSelect(item.id)}
            title={collapsed ? item.name : undefined}
          >
            <Database size={18} strokeWidth={1.7} />
            {!collapsed && (
              <>
                <span className="nav-row-label">{item.name}</span>
                <span className="nav-row-count">{item.documentCount}</span>
              </>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
