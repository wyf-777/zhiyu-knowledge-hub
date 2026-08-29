import { BookOpenText, FileUp, MessageSquareText } from 'lucide-react';

export function EmptyState({ knowledgeBaseName }: { knowledgeBaseName: string }) {
  return (
    <div className="empty-state">
      <span className="empty-state-icon"><BookOpenText size={27} /></span>
      <h1>向“{knowledgeBaseName}”提问</h1>
      <p>我会仅根据当前知识库中的内容回答，并为关键信息提供可追溯的原文依据。</p>
      <div className="empty-hints">
        <span><MessageSquareText size={15} />询问制度与流程</span>
        <span><FileUp size={15} />输入 @ 指定文档</span>
      </div>
    </div>
  );
}
