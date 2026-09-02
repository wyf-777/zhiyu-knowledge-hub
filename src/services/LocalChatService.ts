import type { AnswerPerformance, ChatMessage, Citation } from '../types';
import type { RuntimeStatus } from './RuntimeService';

export interface LocalStreamResult {
  citations: Citation[];
  suggestions: string[];
  noEvidence: boolean;
  provider: string;
  performance: AnswerPerformance;
}

interface StreamEvent {
  event: string;
  delta?: string;
  no_evidence?: boolean;
  suggestions?: string[];
  provider?: string;
  timings_ms?: {
    planning?: number;
    retrieval?: number;
    answer_setup?: number;
    before_stream?: number;
    first_answer_delta?: number | null;
    generation?: number;
    total?: number;
  };
  diagnostics?: {
    query_context?: AnswerPerformance['queryContext'];
    query_rewritten?: boolean;
    retrieval_query_count?: number;
    retrieval_candidate_count?: number;
    embedding_execution_mode?: string;
    embedding_model?: string;
    answer_path?: AnswerPerformance['answerPath'];
    validation?: string;
    validation_retry_count?: number;
    used_extractive_fallback?: boolean;
    model_cpu_fallback?: boolean;
    model_load_ms?: number;
    prompt_tokens?: number;
    generated_tokens?: number;
    tokens_per_second?: number | null;
    citation_count?: number;
    visible_character_count?: number;
  };
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
    let startTimings: NonNullable<StreamEvent['timings_ms']> = {};
    let completionTimings: NonNullable<StreamEvent['timings_ms']> = {};
    let startDiagnostics: NonNullable<StreamEvent['diagnostics']> = {};
    let completionDiagnostics: NonNullable<StreamEvent['diagnostics']> = {};
    let completed = false;
    let buffer = '';
    const decoder = new TextDecoder();
    const reader = response.body.getReader();

    const handleLine = (line: string) => {
      if (!line.trim()) return;
      const event = JSON.parse(line) as StreamEvent;
      if (event.event === 'message.start') {
        if (event.provider) provider = event.provider;
        startTimings = event.timings_ms ?? {};
        startDiagnostics = event.diagnostics ?? {};
      }
      if (event.event === 'citation' && event.citation) citations.push(toCitation(event.citation));
      if (event.event === 'message.delta' && event.delta) onChunk(event.delta);
      if (event.event === 'message.completed') {
        suggestions = event.suggestions ?? [];
        noEvidence = Boolean(event.no_evidence);
        provider = event.provider ?? provider;
        completionTimings = event.timings_ms ?? {};
        completionDiagnostics = event.diagnostics ?? {};
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
    const diagnostics = { ...startDiagnostics, ...completionDiagnostics };
    return {
      citations,
      suggestions,
      noEvidence,
      provider,
      performance: {
        provider,
        timingsMs: {
          planning: startTimings.planning,
          retrieval: startTimings.retrieval,
          answerSetup: startTimings.answer_setup,
          beforeStream: startTimings.before_stream,
          firstAnswerDelta: completionTimings.first_answer_delta,
          generation: completionTimings.generation,
          total: completionTimings.total,
        },
        queryContext: diagnostics.query_context,
        queryRewritten: diagnostics.query_rewritten,
        retrievalQueryCount: diagnostics.retrieval_query_count,
        retrievalCandidateCount: diagnostics.retrieval_candidate_count,
        embeddingExecutionMode: diagnostics.embedding_execution_mode,
        embeddingModel: diagnostics.embedding_model,
        answerPath: diagnostics.answer_path,
        validation: diagnostics.validation,
        validationRetryCount: diagnostics.validation_retry_count,
        usedExtractiveFallback: diagnostics.used_extractive_fallback,
        modelCpuFallback: diagnostics.model_cpu_fallback,
        modelLoadMs: diagnostics.model_load_ms,
        promptTokens: diagnostics.prompt_tokens,
        generatedTokens: diagnostics.generated_tokens,
        tokensPerSecond: diagnostics.tokens_per_second,
        citationCount: diagnostics.citation_count,
        visibleCharacterCount: diagnostics.visible_character_count,
      },
    };
  }
}

export const localChatService = new LocalChatService();
