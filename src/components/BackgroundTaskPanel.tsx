import { AlertTriangle, CheckCircle2, CircleDashed, LoaderCircle, RefreshCw, X } from 'lucide-react';
import type { BackgroundTask, TaskStepStatus } from '../types';

const stepLabel = { upload: '上传', ocr: 'OCR', chunk: '切分', index: '向量索引' } as const;

function StepIcon({ status }: { status: TaskStepStatus }) {
  if (status === 'success') return <CheckCircle2 size={14} />;
  if (status === 'failed') return <AlertTriangle size={14} />;
  if (status === 'running') return <LoaderCircle size={14} className="spin" />;
  return <CircleDashed size={14} />;
}

export function BackgroundTaskPanel({
  open,
  tasks,
  citationOpen,
  onClose,
  onRetry,
}: {
  open: boolean;
  tasks: BackgroundTask[];
  citationOpen: boolean;
  onClose: () => void;
  onRetry: (id: string) => void;
}) {
  if (!open) return null;
  return (
    <section className={`background-task-panel glass ${citationOpen ? 'avoid-citation' : ''}`}>
      <header className="popover-heading"><span>后台任务</span><button className="icon-button compact" onClick={onClose} aria-label="关闭后台任务"><X size={17} /></button></header>
      <div className="task-list">
        {tasks.map((task) => (
          <article className="task-card" key={task.id}>
            <div className="task-card-heading"><strong>{task.fileName}</strong><span>{task.progress}%</span></div>
            <p>{task.stage}</p>
            <div className="task-progress"><i style={{ width: `${task.progress}%` }} /></div>
            <div className="task-steps">
              {(Object.keys(stepLabel) as (keyof typeof stepLabel)[]).map((step) => (
                <span key={step} className={`step-${task.steps[step]}`}><StepIcon status={task.steps[step]} />{stepLabel[step]}</span>
              ))}
            </div>
            {task.errorMessage && <p className="task-error-message"><AlertTriangle size={13} />{task.errorMessage}</p>}
            {task.status === 'failed' && <button className="retry-task" onClick={() => onRetry(task.id)}><RefreshCw size={14} />重试</button>}
          </article>
        ))}
      </div>
    </section>
  );
}
