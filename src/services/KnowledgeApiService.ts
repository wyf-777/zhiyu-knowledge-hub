import type { BackgroundTask, ChatMessage, Conversation, KnowledgeBase, KnowledgeDocument, RetrievalResult, TaskStepStatus } from '../types';
import type { EmbeddingProvider, EmbeddingState, ModelProvider, ModelState, RerankerProvider, RerankerState, RuntimeStatus } from './RuntimeService';

export interface LocalModelSettings {
  provider: ModelProvider;
  baseUrl: string;
  model: string;
  timeoutSeconds: number;
}

export interface LocalModelStatus {
  provider: ModelProvider;
  state: ModelState;
  model: string;
  baseUrl: string;
  message: string;
  executionMode: string;
}

interface ApiModelSettings {
  provider: ModelProvider;
  base_url: string;
  model: string;
  timeout_seconds: number;
}

interface ApiModelStatus {
  provider: ModelProvider;
  state: ModelState;
  model: string;
  base_url: string;
  message: string;
  execution_mode: string;
}

interface ApiEmbeddingStatus {
  provider: EmbeddingProvider;
  state: EmbeddingState;
  model_id: string;
  dimensions: number;
  index_version: number;
  execution_mode: string;
  message: string;
}

interface ApiRerankerStatus {
  provider: RerankerProvider;
  state: RerankerState;
  model_id: string;
  message: string;
}

interface ApiOcrStatus {
  provider: string;
  state: 'ready' | 'unavailable' | 'error';
  execution_mode: string;
  message: string;
}

interface ApiIndexSummary {
  embedding_model_id: string;
  embedding_dimensions: number;
  index_version: number;
  chunk_count: number;
  document_count: number;
}

interface ApiKnowledgeBase {
  id: string;
  name: string;
  document_count: number;
  updated_at: string;
}

interface ApiDocument {
  id: string;
  knowledge_base_id: string;
  source_name: string;
  source_type: KnowledgeDocument['sourceType'];
  size_bytes: number;
  status: KnowledgeDocument['status'];
  chunk_count: number;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

interface ApiJob {
  id: string;
  document_id: string;
  source_name: string;
  stage: string;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'canceled';
  progress: number;
  error_message?: string | null;
}

interface ApiSearchResult {
  chunk_id: string;
  document_id: string;
  document_name: string;
  format: string;
  page: number | null;
  heading: string;
  content: string;
  parent_chunk_id?: string;
  parent_content?: string;
  content_kind?: 'text' | 'table' | 'ocr';
  location?: RetrievalResult['location'];
  score: number;
  updated_at: string;
}

interface ApiConversation {
  id: string;
  knowledge_base_id: string;
  title: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
}

interface ApiChatMessage {
  id: string;
  role: ChatMessage['role'];
  content: string;
  time: string;
  citations?: ChatMessage['citations'] | null;
  suggestions?: string[] | null;
  status?: ChatMessage['status'] | null;
  feedback?: ChatMessage['feedback'] | null;
}

export interface StorageStatus {
  schemaVersion: number;
  currentSchemaVersion: number;
  integrity: string;
  dataDirectory: string;
  backupDirectory: string;
  conversationCount: number;
}

export interface LocalBackup {
  id: string;
  createdAt: string;
  sizeBytes: number;
  schemaVersion?: number;
  documentFileCount: number;
  state: 'valid' | 'invalid';
  message: string;
}

interface ApiBackup {
  id: string;
  created_at: string;
  size_bytes: number;
  schema_version?: number | null;
  document_file_count: number;
  state: 'valid' | 'invalid';
  message: string;
}

function formatUpdatedLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '刚刚';
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
}

function toConversation(item: ApiConversation): Conversation {
  return {
    id: item.id,
    knowledgeBaseId: item.knowledge_base_id,
    title: item.title,
    pinned: item.pinned,
    updatedAt: formatUpdatedLabel(item.updated_at),
  };
}

function toChatMessage(item: ApiChatMessage): ChatMessage {
  return {
    id: item.id,
    role: item.role,
    content: item.content,
    time: item.time,
    citations: item.citations ?? undefined,
    suggestions: item.suggestions ?? undefined,
    status: item.status ?? undefined,
    feedback: item.feedback ?? undefined,
  };
}

function toBackup(item: ApiBackup): LocalBackup {
  return {
    id: item.id,
    createdAt: item.created_at,
    sizeBytes: item.size_bytes,
    schemaVersion: item.schema_version ?? undefined,
    documentFileCount: item.document_file_count,
    state: item.state,
    message: item.message,
  };
}

function connection(status: RuntimeStatus) {
  if (!status.baseUrl || !status.token || status.phase !== 'running') {
    throw new Error(status.phase === 'web-preview' ? '请在桌面版中使用真实文档管理功能。' : '本地知识库服务尚未就绪。');
  }
  return { baseUrl: status.baseUrl, token: status.token };
}

async function request<T>(status: RuntimeStatus, path: string, init?: RequestInit): Promise<T> {
  const { baseUrl, token } = connection(status);
  const headers = new Headers(init?.headers);
  headers.set('Authorization', `Bearer ${token}`);
  if (init?.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${baseUrl}${path}`, { ...init, headers });
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null) as { error?: { message?: string } } | null;
  if (!response.ok) throw new Error(body?.error?.message ?? `本地服务请求失败（${response.status}）`);
  return body as T;
}

const stageLabels: Record<string, string> = {
  queued: '等待处理',
  parse: '正在解析文档',
  ocr: '正在进行本地 OCR',
  chunk: '正在结构化切分',
  embedding: '正在生成本地向量',
  index: '正在写入向量索引',
  completed: '处理完成',
  failed: '处理失败',
};

function taskSteps(job: ApiJob): BackgroundTask['steps'] {
  const waiting: TaskStepStatus = 'waiting';
  if (job.status === 'succeeded') return { upload: 'success', ocr: 'success', chunk: 'success', index: 'success' };
  if (job.status === 'failed') return { upload: 'success', ocr: 'failed', chunk: waiting, index: waiting };
  if (job.stage === 'queued') return { upload: 'success', ocr: waiting, chunk: waiting, index: waiting };
  if (job.stage === 'parse') return { upload: 'success', ocr: waiting, chunk: waiting, index: waiting };
  if (job.stage === 'ocr') return { upload: 'success', ocr: 'running', chunk: waiting, index: waiting };
  if (job.stage === 'chunk') return { upload: 'success', ocr: 'success', chunk: 'running', index: waiting };
  return { upload: 'success', ocr: 'success', chunk: 'success', index: 'running' };
}

function toModelSettings(settings: ApiModelSettings): LocalModelSettings {
  return {
    provider: settings.provider,
    baseUrl: settings.base_url,
    model: settings.model,
    timeoutSeconds: settings.timeout_seconds,
  };
}

function toModelStatus(status: ApiModelStatus): LocalModelStatus {
  return {
    provider: status.provider,
    state: status.state,
    model: status.model,
    baseUrl: status.base_url,
    message: status.message,
    executionMode: status.execution_mode,
  };
}

export function jobToBackgroundTask(job: ApiJob): BackgroundTask {
  return {
    id: job.id,
    documentId: job.document_id,
    fileName: job.source_name,
    progress: job.progress,
    stage: stageLabels[job.stage] ?? job.stage,
    status: job.status === 'succeeded' ? 'success' : job.status === 'failed' ? 'failed' : job.status === 'pending' ? 'waiting' : 'running',
    steps: taskSteps(job),
    errorMessage: job.error_message ?? undefined,
  };
}

class KnowledgeApiService {
  async getRuntimeDetails(status: RuntimeStatus): Promise<Partial<RuntimeStatus>> {
    const response = await request<{
      model: ApiModelStatus;
      embedding: ApiEmbeddingStatus;
      reranker: ApiRerankerStatus;
      ocr: ApiOcrStatus;
      index_mode: string;
      indexes: ApiIndexSummary[];
    }>(status, '/api/v1/runtime/status');
    const model = toModelStatus(response.model);
    return {
      modelProvider: model.provider,
      modelState: model.state,
      modelName: model.model,
      modelBaseUrl: model.baseUrl,
      modelMessage: model.message,
      modelExecutionMode: model.executionMode,
      embeddingProvider: response.embedding.provider,
      embeddingState: response.embedding.state,
      embeddingModelId: response.embedding.model_id,
      embeddingDimensions: response.embedding.dimensions,
      embeddingIndexVersion: response.embedding.index_version,
      embeddingExecutionMode: response.embedding.execution_mode,
      embeddingMessage: response.embedding.message,
      rerankerProvider: response.reranker.provider,
      rerankerState: response.reranker.state,
      rerankerModelId: response.reranker.model_id,
      rerankerMessage: response.reranker.message,
      ocrProvider: response.ocr.provider,
      ocrState: response.ocr.state,
      ocrExecutionMode: response.ocr.execution_mode,
      ocrMessage: response.ocr.message,
      indexMode: response.index_mode,
      indexSummaries: response.indexes.map((item) => ({
        embeddingModelId: item.embedding_model_id,
        embeddingDimensions: item.embedding_dimensions,
        indexVersion: item.index_version,
        chunkCount: item.chunk_count,
        documentCount: item.document_count,
      })),
    };
  }

  async getCitationPreview(status: RuntimeStatus, documentId: string, chunkId: string): Promise<Blob> {
    const { baseUrl, token } = connection(status);
    const response = await fetch(`${baseUrl}/api/v1/documents/${encodeURIComponent(documentId)}/chunks/${encodeURIComponent(chunkId)}/preview`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { error?: { message?: string } } | null;
      throw new Error(body?.error?.message ?? `无法生成定位预览（${response.status}）`);
    }
    return response.blob();
  }

  async getModelSettings(status: RuntimeStatus): Promise<{ settings: LocalModelSettings; status: LocalModelStatus }> {
    const response = await request<{ settings: ApiModelSettings; status: ApiModelStatus }>(status, '/api/v1/model/settings');
    return { settings: toModelSettings(response.settings), status: toModelStatus(response.status) };
  }

  async updateModelSettings(status: RuntimeStatus, settings: LocalModelSettings): Promise<{ settings: LocalModelSettings; status: LocalModelStatus }> {
    const response = await request<{ settings: ApiModelSettings; status: ApiModelStatus }>(status, '/api/v1/model/settings', {
      method: 'PUT',
      body: JSON.stringify({
        provider: settings.provider,
        base_url: settings.baseUrl,
        model: settings.model,
        timeout_seconds: settings.timeoutSeconds,
      }),
    });
    return { settings: toModelSettings(response.settings), status: toModelStatus(response.status) };
  }

  async detectModel(status: RuntimeStatus): Promise<LocalModelStatus> {
    const response = await request<{ status: ApiModelStatus }>(status, '/api/v1/model/detect', { method: 'POST' });
    return toModelStatus(response.status);
  }

  async listConversations(status: RuntimeStatus): Promise<{ conversations: Conversation[]; messages: Record<string, ChatMessage[]> }> {
    const response = await request<{ items: ApiConversation[] }>(status, '/api/v1/conversations');
    const pairs = await Promise.all(response.items.map(async (conversation) => {
      const messageResponse = await request<{ items: ApiChatMessage[] }>(
        status,
        `/api/v1/conversations/${encodeURIComponent(conversation.id)}/messages`,
      );
      return [conversation.id, messageResponse.items.map(toChatMessage)] as const;
    }));
    return { conversations: response.items.map(toConversation), messages: Object.fromEntries(pairs) };
  }

  async createConversation(status: RuntimeStatus, conversation: Conversation): Promise<Conversation> {
    const response = await request<{ conversation: ApiConversation }>(status, '/api/v1/conversations', {
      method: 'POST',
      body: JSON.stringify({
        id: conversation.id,
        knowledge_base_id: conversation.knowledgeBaseId,
        title: conversation.title,
        pinned: Boolean(conversation.pinned),
      }),
    });
    return toConversation(response.conversation);
  }

  async updateConversation(status: RuntimeStatus, conversationId: string, changes: { title?: string; pinned?: boolean }): Promise<Conversation> {
    const response = await request<{ conversation: ApiConversation }>(status, `/api/v1/conversations/${encodeURIComponent(conversationId)}`, {
      method: 'PUT',
      body: JSON.stringify(changes),
    });
    return toConversation(response.conversation);
  }

  async deleteConversation(status: RuntimeStatus, conversationId: string) {
    return request<void>(status, `/api/v1/conversations/${encodeURIComponent(conversationId)}`, { method: 'DELETE' });
  }

  async replaceConversationMessages(status: RuntimeStatus, conversationId: string, messages: ChatMessage[]) {
    return request<{ items: ApiChatMessage[] }>(status, `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`, {
      method: 'PUT',
      body: JSON.stringify({ items: messages }),
    });
  }

  async importLegacyConversations(
    status: RuntimeStatus,
    conversations: Conversation[],
    messages: Record<string, ChatMessage[]>,
  ): Promise<{ imported: boolean; conversation_count: number; imported_at: string }> {
    return request(status, '/api/v1/conversations/import', {
      method: 'POST',
      body: JSON.stringify({
        import_id: 'webview-localstorage-v1',
        conversations: conversations.map((item) => ({
          id: item.id,
          knowledge_base_id: item.knowledgeBaseId,
          title: item.title,
          pinned: Boolean(item.pinned),
        })),
        messages,
      }),
    });
  }

  async getStorageStatus(status: RuntimeStatus): Promise<StorageStatus> {
    const response = await request<{
      schema: { schema_version: number; current_schema_version: number; integrity: string };
      data_directory: string;
      backup_directory: string;
      conversation_count: number;
    }>(status, '/api/v1/storage/status');
    return {
      schemaVersion: response.schema.schema_version,
      currentSchemaVersion: response.schema.current_schema_version,
      integrity: response.schema.integrity,
      dataDirectory: response.data_directory,
      backupDirectory: response.backup_directory,
      conversationCount: response.conversation_count,
    };
  }

  async listBackups(status: RuntimeStatus): Promise<LocalBackup[]> {
    const response = await request<{ items: ApiBackup[] }>(status, '/api/v1/storage/backups');
    return response.items.map(toBackup);
  }

  async createBackup(status: RuntimeStatus): Promise<LocalBackup> {
    const response = await request<{ backup: ApiBackup }>(status, '/api/v1/storage/backups', { method: 'POST' });
    return toBackup(response.backup);
  }

  async importBackup(status: RuntimeStatus, file: File): Promise<LocalBackup> {
    const form = new FormData();
    form.append('backup', file, file.name);
    const response = await request<{ backup: ApiBackup }>(status, '/api/v1/storage/backups/import', { method: 'POST', body: form });
    return toBackup(response.backup);
  }

  async restoreBackup(status: RuntimeStatus, backupId: string): Promise<{ restored: boolean; safety_backup_id?: string }> {
    return request(status, `/api/v1/storage/backups/${encodeURIComponent(backupId)}/restore`, { method: 'POST' });
  }

  async openBackupFolder(status: RuntimeStatus) {
    return request(status, '/api/v1/storage/backups/open-folder', { method: 'POST' });
  }

  async listKnowledgeBases(status: RuntimeStatus): Promise<KnowledgeBase[]> {
    const response = await request<{ items: ApiKnowledgeBase[] }>(status, '/api/v1/knowledge-bases');
    return response.items.map((item) => ({
      id: item.id,
      name: item.name,
      documentCount: item.document_count,
      updatedLabel: formatUpdatedLabel(item.updated_at),
    }));
  }

  async listDocuments(status: RuntimeStatus, knowledgeBaseId: string): Promise<KnowledgeDocument[]> {
    const response = await request<{ items: ApiDocument[] }>(status, `/api/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents`);
    return response.items.map((item) => ({
      id: item.id,
      knowledgeBaseId: item.knowledge_base_id,
      sourceName: item.source_name,
      sourceType: item.source_type,
      sizeBytes: item.size_bytes,
      status: item.status,
      chunkCount: item.chunk_count,
      errorMessage: item.error_message ?? undefined,
      createdAt: item.created_at,
      updatedAt: item.updated_at,
    }));
  }

  async uploadDocuments(status: RuntimeStatus, knowledgeBaseId: string, files: File[]) {
    const form = new FormData();
    files.forEach((file) => form.append('files', file, file.name));
    return request(status, `/api/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/documents`, { method: 'POST', body: form });
  }

  async deleteDocument(status: RuntimeStatus, documentId: string) {
    return request<void>(status, `/api/v1/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE' });
  }

  async openDocument(status: RuntimeStatus, documentId: string) {
    return request<{ status: 'opened' }>(status, `/api/v1/documents/${encodeURIComponent(documentId)}/open`, { method: 'POST' });
  }

  async retryDocument(status: RuntimeStatus, documentId: string) {
    return request(status, `/api/v1/documents/${encodeURIComponent(documentId)}/retry`, { method: 'POST' });
  }

  async reindexKnowledgeBase(status: RuntimeStatus, knowledgeBaseId: string) {
    return request<{ items: ApiJob[] }>(status, `/api/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/reindex`, { method: 'POST' });
  }

  async listTasks(status: RuntimeStatus): Promise<BackgroundTask[]> {
    const response = await request<{ items: ApiJob[] }>(status, '/api/v1/jobs?limit=50');
    const seen = new Set<string>();
    return response.items.filter((job) => {
      if (seen.has(job.document_id)) return false;
      seen.add(job.document_id);
      return true;
    }).map(jobToBackgroundTask);
  }

  async search(status: RuntimeStatus, knowledgeBaseId: string, query: string, documentIds: string[] = []): Promise<RetrievalResult[]> {
    const response = await request<{ items: ApiSearchResult[] }>(status, '/api/v1/retrieval/search', {
      method: 'POST',
      body: JSON.stringify({ query, knowledge_base_ids: [knowledgeBaseId], document_ids: documentIds, limit: 8 }),
    });
    return response.items.map((item) => ({
      chunkId: item.chunk_id,
      documentId: item.document_id,
      documentName: item.document_name,
      format: item.format,
      page: item.page,
      heading: item.heading,
      content: item.content,
      parentChunkId: item.parent_chunk_id,
      parentContent: item.parent_content,
      contentKind: item.content_kind,
      location: item.location,
      score: item.score,
      updatedAt: item.updated_at,
    }));
  }
}

export const knowledgeApiService = new KnowledgeApiService();
