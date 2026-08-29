import { AlertTriangle, RefreshCw } from 'lucide-react';

export function ErrorState({ title, detail, onRetry }: { title: string; detail: string; onRetry?: () => void }) {
  return (
    <div className="error-state" role="alert">
      <AlertTriangle size={20} />
      <div><strong>{title}</strong><p>{detail}</p></div>
      {onRetry && <button onClick={onRetry}><RefreshCw size={15} />重试</button>}
    </div>
  );
}
