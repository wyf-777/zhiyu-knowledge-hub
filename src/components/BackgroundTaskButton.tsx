import { Activity, ChevronUp } from 'lucide-react';

interface BackgroundTaskButtonProps {
  count: number;
  progress: number;
  open: boolean;
  citationOpen: boolean;
  onClick: () => void;
}

export function BackgroundTaskButton({ count, progress, open, citationOpen, onClick }: BackgroundTaskButtonProps) {
  return (
    <button
      className={`background-task-button glass ${citationOpen ? 'avoid-citation' : ''}`}
      onClick={onClick}
      aria-expanded={open}
    >
      <Activity size={17} />
      <span>{count} 个后台任务</span>
      <span className="mini-progress"><i style={{ width: `${progress}%` }} /></span>
      <strong>{progress}%</strong>
      <ChevronUp size={16} className={open ? 'is-rotated' : ''} />
    </button>
  );
}
