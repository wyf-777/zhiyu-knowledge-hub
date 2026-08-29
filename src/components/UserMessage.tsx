import type { ChatMessage } from '../types';

export function UserMessage({ message }: { message: ChatMessage }) {
  return (
    <article className="user-message-wrap" data-question-id={message.id}>
      <div className="user-bubble">
        <p>{message.content}</p>
        <span>{message.time}　✓✓</span>
      </div>
    </article>
  );
}
