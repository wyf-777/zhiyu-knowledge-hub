import { useEffect, useState } from 'react';
import { Copy, Minus, Square, X } from 'lucide-react';
import { getCurrentWindow } from '@tauri-apps/api/window';

export function DesktopWindowControls() {
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    const appWindow = getCurrentWindow();
    let disposed = false;

    const refreshMaximizedState = async () => {
      const next = await appWindow.isMaximized();
      if (!disposed) setMaximized(next);
    };

    void refreshMaximizedState();
    const unlistenPromise = appWindow.onResized(() => void refreshMaximizedState());

    return () => {
      disposed = true;
      void unlistenPromise.then((unlisten) => unlisten());
    };
  }, []);

  const stopDrag = (event: React.PointerEvent<HTMLButtonElement>) => event.stopPropagation();

  return (
    <div className="desktop-window-controls" aria-label="窗口控制">
      <button
        type="button"
        aria-label="最小化窗口"
        onPointerDown={stopDrag}
        onClick={() => void getCurrentWindow().minimize()}
      >
        <Minus size={15} strokeWidth={1.5} />
      </button>
      <button
        type="button"
        aria-label={maximized ? '还原窗口' : '最大化窗口'}
        onPointerDown={stopDrag}
        onClick={() => void getCurrentWindow().toggleMaximize()}
      >
        {maximized ? <Copy className="restore-window-icon" size={13} strokeWidth={1.45} /> : <Square size={12} strokeWidth={1.5} />}
      </button>
      <button
        type="button"
        aria-label="关闭窗口"
        onPointerDown={stopDrag}
        onClick={() => void getCurrentWindow().close()}
      >
        <X size={16} strokeWidth={1.45} />
      </button>
    </div>
  );
}
