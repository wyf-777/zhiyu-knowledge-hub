import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChatMessage } from '../types';
import { AssistantMessage } from './AssistantMessage';
import { EmptyState } from './EmptyState';
import { UserMessage } from './UserMessage';
import { ErrorState } from './ErrorState';

interface ChatViewProps {
  messages: ChatMessage[];
  knowledgeBaseName: string;
  onOpenCitation: (id: number) => void;
  serviceError: boolean;
  onRetryService: () => void;
  onCopy: (message: ChatMessage) => void;
  onRegenerate: () => void;
  onFeedback: (id: string, value: 'helpful' | 'unhelpful') => void;
  onSuggestion: (text: string) => void;
}

export function ChatView({ messages, knowledgeBaseName, onOpenCitation, serviceError, onRetryService, onCopy, onRegenerate, onFeedback, onSuggestion }: ChatViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const questionTurns = useMemo(() => messages.flatMap((message, index) => {
    if (message.role !== 'user') return [];
    const following = messages.slice(index + 1);
    const nextQuestionIndex = following.findIndex((item) => item.role === 'user');
    const currentTurn = nextQuestionIndex === -1 ? following : following.slice(0, nextQuestionIndex);
    const answer = currentTurn.find((item) => item.role === 'assistant');
    const preview = answer?.content.replace(/\[\[\d+\]\]/g, '').replace(/\s+/g, ' ').trim() ?? '';
    return [{ question: message, preview }];
  }), [messages]);
  const [activeQuestionId, setActiveQuestionId] = useState<string | null>(null);
  const [hoveredQuestionId, setHoveredQuestionId] = useState<string | null>(null);
  const hoveredQuestionIndex = questionTurns.findIndex((turn) => turn.question.id === hoveredQuestionId);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container || questionTurns.length === 0) {
      setActiveQuestionId(null);
      return;
    }

    const updateActiveQuestion = () => {
      const threshold = container.getBoundingClientRect().top + Math.min(180, container.clientHeight * 0.32);
      let activeId = questionTurns[0].question.id;
      for (const turn of questionTurns) {
        const element = container.querySelector<HTMLElement>(`[data-question-id="${turn.question.id}"]`);
        if (element && element.getBoundingClientRect().top <= threshold) activeId = turn.question.id;
      }
      setActiveQuestionId(activeId);
    };

    updateActiveQuestion();
    container.addEventListener('scroll', updateActiveQuestion, { passive: true });
    return () => container.removeEventListener('scroll', updateActiveQuestion);
  }, [questionTurns]);

  const jumpToQuestion = (id: string) => {
    const container = scrollRef.current;
    const target = container?.querySelector<HTMLElement>(`[data-question-id="${id}"]`);
    if (!container || !target) return;
    setActiveQuestionId(id);
    const top = container.scrollTop + target.getBoundingClientRect().top - container.getBoundingClientRect().top - 24;
    container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  };

  if (!messages.length && !serviceError) return <EmptyState knowledgeBaseName={knowledgeBaseName} />;

  return (
    <>
      <div className="chat-scroll" ref={scrollRef}>
        <div className="chat-content">
          {serviceError && <ErrorState title="本地服务暂时不可用" detail="请确认本地知识库服务正在运行，然后重试。" onRetry={onRetryService} />}
          {messages.map((message) =>
            message.role === 'user' ? (
              <UserMessage key={message.id} message={message} />
            ) : (
              <AssistantMessage key={message.id} message={message} onOpenCitation={onOpenCitation} onCopy={onCopy} onRegenerate={onRegenerate} onFeedback={onFeedback} onSuggestion={onSuggestion} />
            ),
          )}
        </div>
      </div>
      {questionTurns.length > 0 && (
        <nav className={`turn-navigation ${hoveredQuestionId ? 'has-hover' : ''}`} aria-label={`对话问题导航，共 ${questionTurns.length} 个问题`}>
          {questionTurns.map(({ question, preview }, index) => {
            const hoverDistance = hoveredQuestionIndex < 0 ? -1 : Math.abs(index - hoveredQuestionIndex);
            const proximityClass = hoverDistance === 0 ? 'is-hovered' : hoverDistance === 1 ? 'is-near-1' : hoverDistance === 2 ? 'is-near-2' : '';
            return (
            <button
              key={question.id}
              className={`${activeQuestionId === question.id ? 'is-active' : ''} ${proximityClass}`.trim()}
              aria-label={`跳转到第 ${index + 1} 个问题：${question.content}`}
              aria-current={activeQuestionId === question.id ? 'true' : undefined}
              onClick={() => jumpToQuestion(question.id)}
              onMouseEnter={() => setHoveredQuestionId(question.id)}
              onMouseLeave={() => setHoveredQuestionId((current) => current === question.id ? null : current)}
              onFocus={() => setHoveredQuestionId(question.id)}
              onBlur={() => setHoveredQuestionId((current) => current === question.id ? null : current)}
            >
              <span className="turn-preview" role="tooltip">
                <strong>{question.content}</strong>
                <span>{preview || '正在等待知识助手回答…'}</span>
              </span>
              <span className="turn-bar" aria-hidden="true" />
            </button>
            );
          })}
        </nav>
      )}
    </>
  );
}
