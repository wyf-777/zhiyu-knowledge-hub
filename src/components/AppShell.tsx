import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent, PointerEvent, ReactNode } from 'react';

const DEFAULT_LEFT_WIDTH = 232;
const DEFAULT_RIGHT_WIDTH = 360;
const LEFT_MIN = 190;
const LEFT_MAX = 380;
const RIGHT_MIN = 300;
const RIGHT_MAX = 560;
const CENTER_MIN = 560;

type ResizeSide = 'left' | 'right';

interface DragState {
  side: ResizeSide;
  startX: number;
  startWidth: number;
  min: number;
  max: number;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

function readSavedWidth(key: string, fallback: number, min: number, max: number) {
  const saved = Number(localStorage.getItem(key));
  return Number.isFinite(saved) && saved >= min && saved <= max ? saved : fallback;
}

interface AppShellProps {
  sidebar: ReactNode;
  topBar: ReactNode;
  children: ReactNode;
  citationDrawer?: ReactNode;
  floatingLayer?: ReactNode;
  sidebarCollapsed: boolean;
  citationOpen: boolean;
}

export function AppShell({
  sidebar,
  topBar,
  children,
  citationDrawer,
  floatingLayer,
  sidebarCollapsed,
  citationOpen,
}: AppShellProps) {
  const shellRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const [leftWidth, setLeftWidth] = useState(() => readSavedWidth('zhiyu-left-column-width', DEFAULT_LEFT_WIDTH, LEFT_MIN, LEFT_MAX));
  const [rightWidth, setRightWidth] = useState(() => readSavedWidth('zhiyu-right-column-width', DEFAULT_RIGHT_WIDTH, RIGHT_MIN, RIGHT_MAX));
  const [resizingSide, setResizingSide] = useState<ResizeSide | null>(null);

  useEffect(() => {
    localStorage.setItem('zhiyu-left-column-width', String(Math.round(leftWidth)));
  }, [leftWidth]);

  useEffect(() => {
    localStorage.setItem('zhiyu-right-column-width', String(Math.round(rightWidth)));
  }, [rightWidth]);

  useEffect(() => () => document.body.classList.remove('is-resizing-columns'), []);

  const getShellWidth = () => shellRef.current?.getBoundingClientRect().width ?? window.innerWidth;

  const getBounds = (side: ResizeSide) => {
    const shellWidth = getShellWidth();
    if (side === 'left') {
      return {
        min: LEFT_MIN,
        max: Math.min(LEFT_MAX, shellWidth - (citationOpen ? rightWidth : 0) - CENTER_MIN),
      };
    }
    return {
      min: RIGHT_MIN,
      max: Math.min(RIGHT_MAX, shellWidth - (sidebarCollapsed ? 0 : leftWidth) - CENTER_MIN),
    };
  };

  const startResize = (side: ResizeSide, event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || window.innerWidth < 1200) return;
    const bounds = getBounds(side);
    dragRef.current = {
      side,
      startX: event.clientX,
      startWidth: side === 'left' ? leftWidth : rightWidth,
      ...bounds,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    document.body.classList.add('is-resizing-columns');
    setResizingSide(side);
    event.preventDefault();
  };

  const continueResize = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const delta = event.clientX - drag.startX;
    const nextWidth = drag.side === 'left' ? drag.startWidth + delta : drag.startWidth - delta;
    if (drag.side === 'left') setLeftWidth(clamp(nextWidth, drag.min, drag.max));
    else setRightWidth(clamp(nextWidth, drag.min, drag.max));
  };

  const stopResize = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    dragRef.current = null;
    document.body.classList.remove('is-resizing-columns');
    setResizingSide(null);
  };

  const resizeWithKeyboard = (side: ResizeSide, event: KeyboardEvent<HTMLDivElement>) => {
    const current = side === 'left' ? leftWidth : rightWidth;
    const bounds = getBounds(side);
    let next: number | null = null;
    if (event.key === 'Home') next = bounds.min;
    if (event.key === 'End') next = bounds.max;
    if (event.key === 'ArrowLeft') next = current + (side === 'left' ? -10 : 10);
    if (event.key === 'ArrowRight') next = current + (side === 'left' ? 10 : -10);
    if (next === null) return;
    event.preventDefault();
    if (side === 'left') setLeftWidth(clamp(next, bounds.min, bounds.max));
    else setRightWidth(clamp(next, bounds.min, bounds.max));
  };

  const shellStyle = {
    '--sidebar-width': `${sidebarCollapsed ? 0 : leftWidth}px`,
    '--citation-width': `${citationOpen ? rightWidth : 0}px`,
  } as CSSProperties;

  return (
    <div
      ref={shellRef}
      style={shellStyle}
      className={`app-shell ${sidebarCollapsed ? 'sidebar-is-collapsed' : ''} ${citationOpen ? 'citation-is-open' : ''} ${resizingSide ? `is-resizing-${resizingSide}` : ''}`}
    >
      {sidebar}
      {!sidebarCollapsed && (
        <div
          className="column-resizer column-resizer-left"
          role="separator"
          aria-label="调整左侧栏宽度"
          aria-orientation="vertical"
          aria-valuemin={LEFT_MIN}
          aria-valuemax={Math.round(getBounds('left').max)}
          aria-valuenow={Math.round(leftWidth)}
          title="拖动调整宽度，双击恢复默认"
          tabIndex={0}
          onPointerDown={(event) => startResize('left', event)}
          onPointerMove={continueResize}
          onPointerUp={stopResize}
          onPointerCancel={stopResize}
          onDoubleClick={() => setLeftWidth(clamp(DEFAULT_LEFT_WIDTH, LEFT_MIN, getBounds('left').max))}
          onKeyDown={(event) => resizeWithKeyboard('left', event)}
        ><span className="resizer-grip" /></div>
      )}
      <section className="workspace">
        {topBar}
        <main className="workspace-main">{children}</main>
      </section>
      {citationOpen && (
        <div
          className="column-resizer column-resizer-right"
          role="separator"
          aria-label="调整回答依据宽度"
          aria-orientation="vertical"
          aria-valuemin={RIGHT_MIN}
          aria-valuemax={Math.round(getBounds('right').max)}
          aria-valuenow={Math.round(rightWidth)}
          title="拖动调整宽度，双击恢复默认"
          tabIndex={0}
          onPointerDown={(event) => startResize('right', event)}
          onPointerMove={continueResize}
          onPointerUp={stopResize}
          onPointerCancel={stopResize}
          onDoubleClick={() => setRightWidth(clamp(DEFAULT_RIGHT_WIDTH, RIGHT_MIN, getBounds('right').max))}
          onKeyDown={(event) => resizeWithKeyboard('right', event)}
        ><span className="resizer-grip" /></div>
      )}
      {citationDrawer}
      {floatingLayer}
    </div>
  );
}
