import { invoke } from '@tauri-apps/api/core';

export type RuntimePhase = 'starting' | 'running' | 'stopped' | 'error' | 'web-preview';

export interface RuntimeStatus {
  phase: RuntimePhase;
  apiRunning: boolean;
  baseUrl: string | null;
  token: string | null;
  serviceVersion: string | null;
  protocolVersion: string | null;
  modelProvider?: ModelProvider;
  modelState?: ModelState;
  modelName?: string;
  modelBaseUrl?: string;
  modelMessage?: string;
  modelExecutionMode?: string;
  embeddingProvider?: EmbeddingProvider;
  embeddingState?: EmbeddingState;
  embeddingModelId?: string;
  embeddingDimensions?: number;
  embeddingIndexVersion?: number;
  embeddingExecutionMode?: string;
  embeddingMessage?: string;
  rerankerProvider?: RerankerProvider;
  rerankerState?: RerankerState;
  rerankerModelId?: string;
  rerankerMessage?: string;
  ocrProvider?: string;
  ocrState?: 'ready' | 'unavailable' | 'error';
  ocrExecutionMode?: string;
  ocrMessage?: string;
  indexMode?: string;
  indexSummaries?: IndexSummary[];
  message: string;
}

export type ModelProvider = 'auto' | 'ollama' | 'openai-compatible' | 'extractive';
export type ModelState = 'ready' | 'service-unavailable' | 'model-missing' | 'extractive' | 'error';
export type EmbeddingProvider = 'ollama' | 'hashing';
export type EmbeddingState = 'ready' | 'fallback' | 'error';
export type RerankerProvider = 'feature' | 'off';
export type RerankerState = 'ready' | 'disabled' | 'error';

export interface IndexSummary {
  embeddingModelId: string;
  embeddingDimensions: number;
  indexVersion: number;
  chunkCount: number;
  documentCount: number;
}

const webPreviewStatus: RuntimeStatus = {
  phase: 'web-preview',
  apiRunning: false,
  baseUrl: null,
  token: null,
  serviceVersion: null,
  protocolVersion: null,
  message: '当前为浏览器预览，桌面版将连接本地知识库服务',
};

function isTauriRuntime() {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

class RuntimeService {
  previewStatus() {
    return webPreviewStatus;
  }

  async getStatus(): Promise<RuntimeStatus> {
    if (!isTauriRuntime()) return webPreviewStatus;
    return invoke<RuntimeStatus>('runtime_status');
  }

  async restart(): Promise<RuntimeStatus> {
    if (!isTauriRuntime()) return webPreviewStatus;
    return invoke<RuntimeStatus>('restart_knowledge_api');
  }
}

export const runtimeService = new RuntimeService();
