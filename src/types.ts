export type ThemeName = 'black' | 'white' | 'gray';

export interface KnowledgeBase {
  id: string;
  name: string;
  documentCount: number;
  updatedLabel: string;
}

export interface Conversation {
  id: string;
  knowledgeBaseId: string;
  title: string;
  pinned?: boolean;
  updatedAt: string;
}

export interface Citation {
  id: number;
  documentId?: string;
  chunkId?: string;
  documentName: string;
  format: 'PDF' | 'DOCX' | 'XLS' | 'XLSX' | 'PPTX' | 'HTML' | 'MD' | 'TXT';
  page: string;
  chapter: string;
  updatedAt: string;
  excerpt: string;
  context: string;
  parentChunkId?: string;
  contentKind?: 'text' | 'table' | 'ocr';
  location?: {
    kind: string;
    page?: number;
    slide?: number;
    sheet?: string;
    bbox?: number[];
    page_width?: number;
    page_height?: number;
  } | null;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  time: string;
  citations?: Citation[];
  suggestions?: string[];
  status?: 'complete' | 'streaming' | 'stopped' | 'no-evidence' | 'error';
  feedback?: 'helpful' | 'unhelpful';
}

export type TaskStepStatus = 'waiting' | 'running' | 'success' | 'failed';
export type BackgroundTaskStatus = 'running' | 'success' | 'failed' | 'waiting';

export interface BackgroundTask {
  id: string;
  documentId?: string;
  fileName: string;
  progress: number;
  stage: string;
  status: BackgroundTaskStatus;
  steps: {
    upload: TaskStepStatus;
    ocr: TaskStepStatus;
    chunk: TaskStepStatus;
    index: TaskStepStatus;
  };
  errorMessage?: string;
}

export type DocumentStatus = 'queued' | 'processing' | 'ready' | 'failed';

export interface KnowledgeDocument {
  id: string;
  knowledgeBaseId: string;
  sourceName: string;
  sourceType: 'pdf' | 'docx' | 'xls' | 'xlsx' | 'pptx' | 'html' | 'txt' | 'md';
  sizeBytes: number;
  status: DocumentStatus;
  chunkCount: number;
  errorMessage?: string;
  createdAt: string;
  updatedAt: string;
}

export interface RetrievalResult {
  chunkId: string;
  documentId: string;
  documentName: string;
  format: string;
  page: number | null;
  heading: string;
  content: string;
  parentChunkId?: string;
  parentContent?: string;
  contentKind?: 'text' | 'table' | 'ocr';
  location?: Citation['location'];
  score: number;
  updatedAt: string;
}
