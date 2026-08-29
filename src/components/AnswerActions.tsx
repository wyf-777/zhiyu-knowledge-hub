import { Copy, RefreshCw, ThumbsDown, ThumbsUp } from 'lucide-react';

interface AnswerActionsProps {
  sourceCount: number;
  feedback?: 'helpful' | 'unhelpful';
  onCopy: () => void;
  onRegenerate: () => void;
  onViewSources: () => void;
  onFeedback: (value: 'helpful' | 'unhelpful') => void;
}

export function AnswerActions({ sourceCount, feedback, onCopy, onRegenerate, onViewSources, onFeedback }: AnswerActionsProps) {
  return (
    <div className="answer-actions">
      <button onClick={onCopy}><Copy size={16} />复制</button>
      {sourceCount > 0 && <button onClick={onViewSources}><RefreshCw size={16} />查看 {sourceCount} 个来源</button>}
      <button className={feedback === 'helpful' ? 'is-selected' : ''} onClick={() => onFeedback('helpful')}><ThumbsUp size={16} />有帮助</button>
      <button className={feedback === 'unhelpful' ? 'is-selected' : ''} onClick={() => onFeedback('unhelpful')}><ThumbsDown size={16} />没帮助</button>
      <button onClick={onRegenerate}><RefreshCw size={16} />重新生成</button>
    </div>
  );
}
