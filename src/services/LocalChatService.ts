import type { ChatMessage, Citation } from '../types';
import type { RuntimeStatus } from './RuntimeService';

export interface LocalStreamResult {
  citations: Citation[];
  suggestions: string[];
  noEvidence: boolean;
  provider: string;
}

interface StreamEvent {
  event: string;
  delta?: string;
  no_evidence?: boolean;
  suggestions?: string[];
  provider?: string;
  citation?: {
    id: number;
    chunk_id: string;
    document_id: string;
    document_name: string;
    format: string;
    page: number | null;
    chapter: string;
    updated_at: string;
    excerpt: string;
    context: string;
    parent_chunk_id?: string;
    content_kind?: 'text' | 'table' | 'ocr';
    location?: Citation['location'];
  };
}

function formatUpdatedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

function toCitation(event: NonNullable<StreamEvent['citation']>): Citation {
  const normalizedFormat = event.format.toUpperCase();
  const supportedFormats: Citation['format'][] = ['PDF', 'DOCX', 'XLS', 'XLSX', 'PPTX', 'HTML', 'TXT', 'MD'];
  const format: Citation['format'] = supportedFormats.includes(normalizedFormat as Citation['format']) ? normalizedFormat as Citation['format'] : 'MD';
  return {
    id: event.id,
    documentId: event.document_id,
    chunkId: event.chunk_id,
    documentName: event.document_name,
    format,
    page: event.page ? format === 'PPTX' ? `第 ${event.page} 张` : `第 ${event.page} 页` : '正文位置',
    chapter: event.chapter || '正文',
    updatedAt: formatUpdatedAt(event.updated_at),
    excerpt: event.excerpt,
    context: event.context,
    parentChunkId: event.parent_chunk_id,
    contentKind: event.content_kind,
    location: event.location,
  };
}

class LocalChatService {
  async streamAnswer(
    question: string,
    knowledgeBaseId: string,
    runtimeStatus: RuntimeStatus,
    documentIds: string[],
    history: Array<Pick<ChatMessage, 'role' | 'content'>>,
    conversationId: string | null,
    signal: AbortSignal,
    onChunk: (chunk: string) => void,
  ): Promise<LocalStreamResult> {
    if (runtimeStatus.phase !== 'running' || !runtimeStatus.baseUrl || !runtimeStatus.token) {
      throw new Error('本地知识库服务尚未就绪。');
    }
    const response = await fetch(`${runtimeStatus.baseUrl}/api/v1/chat/completions`, {
      method: 'POST',
      signal,
      headers: {
        Authorization: `Bearer ${runtimeStatus.token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message: question,
        knowledge_base_ids: [knowledgeBaseId],
        document_ids: documentIds,
        conversation_id: conversationId,
        history: history.slice(-10),
        stream: true,
      }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => null) as { error?: { message?: string } } | null;
      throw new Error(error?.error?.message ?? `本地问答请求失败（${response.status}）`);
    }
    if (!response.body) throw new Error('本地服务没有返回可读取的回答流。');

    const citations: Citation[] = [];
    let suggestions: string[] = [];
    let noEvidence = false;
    let provider = 'unknown';
    let completed = false;
    let buffer = '';
    const decoder = new TextDecoder();
    const reader = response.body.getReader();

    const handleLine = (line: string) => {
      if (!line.trim()) return;
      const event = JSON.parse(line) as StreamEvent;
      if (event.event === 'message.start' && event.provider) provider = event.provider;
      if (event.event === 'citation' && event.citation) citations.push(toCitation(event.citation));
      if (event.event === 'message.delta' && event.delta) onChunk(event.delta);
      if (event.event === 'message.completed') {
        suggestions = event.suggestions ?? [];
        noEvidence = Boolean(event.no_evidence);
        provider = event.provider ?? provider;
        completed = true;
      }
    };

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      lines.forEach(handleLine);
      if (done) break;
    }
    if (buffer.trim()) handleLine(buffer);
    if (!completed) throw new Error('本地回答流意外中断。');
    return { citations, suggestions, noEvidence, provider };
  }
}

export const localChatService = new LocalChatService();
