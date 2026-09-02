import { useState } from 'react';
import { BookOpen, Box, LoaderCircle, Sparkles, Square } from 'lucide-react';
import type { ChatMessage } from '../types';
import { AnswerActions } from './AnswerActions';
import { AnswerDiagnostics } from './AnswerDiagnostics';
import { CitationMarker } from './CitationMarker';

interface AssistantMessageProps {
  message: ChatMessage;
  onOpenCitation: (id: number) => void;
  onCopy: (message: ChatMessage) => void;
  onRegenerate: () => void;
  onFeedback: (id: string, value: 'helpful' | 'unhelpful') => void;
  onSuggestion: (text: string) => void;
}

function renderParagraph(text: string, citations: ChatMessage['citations'], onOpenCitation: (id: number) => void) {
  const parts = text.split(/(\[\[\d+\]\]|\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, index) => {
    const citationMatch = part.match(/^\[\[(\d+)\]\]$/);
    const citation = citationMatch ? citations?.find((item) => item.id === Number(citationMatch[1])) : undefined;
    if (citationMatch) return citation ? <CitationMarker key={`${part}-${index}`} citation={citation} onOpen={onOpenCitation} /> : null;
    const emphasized = part.startsWith('**') && part.endsWith('**');
    const cleanPart = emphasized ? part.slice(2, -2) : part;
    return (
      <span key={`${part}-${index}`}>
        {emphasized ? <strong>{cleanPart}</strong> : cleanPart}
      </span>
    );
  });
}

export function AssistantMessage({ message, onOpenCitation, onCopy, onRegenerate, onFeedback, onSuggestion }: AssistantMessageProps) {
  const [performanceOpen, setPerformanceOpen] = useState(false);
  const blocks = message.content.split('\n\n');
  return (
    <article className="assistant-message">
      <header className="assistant-heading">
        <span className="assistant-avatar"><Box size={18} /></span>
        <strong>知识助手</strong>
        <time>{message.time}</time>
      </header>
      <div className="assistant-content">
        {blocks.map((block, index) => {
          if (block.startsWith('- ')) {
            return (
              <ul key={index}>
                {block.split('\n').map((item) => (
                  <li key={item}>{renderParagraph(item.replace(/^- /, ''), message.citations, onOpenCitation)}</li>
                ))}
              </ul>
            );
          }
          return <p key={index}>{renderParagraph(block, message.citations, onOpenCitation)}</p>;
        })}
      </div>
      {message.status === 'streaming' && (
        <div className="generation-status">
          <LoaderCircle size={15} className="spin" />
          {message.streamPhase === 'sources-ready' && (message.retrievalSourceCount ?? 0) > 0
            ? '检索完成，正在生成并校验回答…'
            : message.streamPhase === 'generating'
              ? '正在生成并校验回答…'
              : '正在检索知识库…'}
          {!!message.citations?.length && (
            <button onClick={() => onOpenCitation(message.citations![0].id)}>
              <BookOpen size={13} />已找到 {message.retrievalSourceCount ?? message.citations.length} 条依据
            </button>
          )}
        </div>
      )}
      {message.status === 'stopped' && <div className="generation-status"><Square size={13} />已停止生成</div>}
      {message.status !== 'streaming' && (
        <AnswerActions
          sourceCount={message.citations?.length ?? 0}
          feedback={message.feedback}
          onCopy={() => onCopy(message)}
          onRegenerate={onRegenerate}
          onViewSources={() => message.citations?.[0] && onOpenCitation(message.citations[0].id)}
          onFeedback={(value) => onFeedback(message.id, value)}
          hasPerformance={Boolean(message.performance)}
          performanceOpen={performanceOpen}
          onPerformanceToggle={() => setPerformanceOpen((value) => !value)}
        />
      )}
      {performanceOpen && message.performance && <AnswerDiagnostics performance={message.performance} />}
      {!!message.suggestions?.length && (
        <div className="suggestions-block">
          <span>推荐追问</span>
          <div className="suggestions-row">
            {message.suggestions.map((suggestion) => (
              <button key={suggestion} onClick={() => onSuggestion(suggestion)}><Sparkles size={14} />{suggestion}</button>
            ))}
          </div>
        </div>
      )}
    </article>
  );
}
