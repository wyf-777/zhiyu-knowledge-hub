import { AlertTriangle, CheckCircle2, Database, FileStack, Layers3, ScanText, ShieldCheck } from 'lucide-react';
import type { RuntimeStatus } from '../services/RuntimeService';

interface RetrievalSettingsPanelProps {
  runtimeStatus: RuntimeStatus;
}

const embeddingStateLabels = {
  ready: '本地 Embedding 已就绪',
  fallback: '正在使用轻量回退索引',
  error: 'Embedding 不可用',
} as const;

const rerankerStateLabels = {
  ready: '本地重排器已启用',
  disabled: '独立重排已关闭',
  error: 'Reranker 不可用',
} as const;

export function RetrievalSettingsPanel({ runtimeStatus }: RetrievalSettingsPanelProps) {
  const summaries = runtimeStatus.indexSummaries ?? [];
  const documentCount = summaries.reduce((total, item) => total + item.documentCount, 0);
  const chunkCount = summaries.reduce((total, item) => total + item.chunkCount, 0);
  const state = runtimeStatus.embeddingState;
  const hasOutdatedIndexes = summaries.some((item) => (
    item.embeddingModelId !== runtimeStatus.embeddingModelId
    || item.embeddingDimensions !== runtimeStatus.embeddingDimensions
    || item.indexVersion !== runtimeStatus.embeddingIndexVersion
  ));
  const ready = runtimeStatus.phase === 'running' && Boolean(state);
  const StatusIcon = state === 'fallback' || state === 'error' || !ready ? AlertTriangle : CheckCircle2;

  return (
    <>
      <section className="settings-section" aria-labelledby="retrieval-status-title">
        <div className="settings-section-heading">
          <h2 id="retrieval-status-title">检索运行状态</h2>
          <p>这里显示当前实际生效的 Embedding 与索引，不提供无法生效的模拟开关。</p>
        </div>
        <div className="retrieval-runtime-stack">
          <div className="model-status-card" role="status">
            <StatusIcon size={19} />
            <div>
              <strong>{ready && state ? embeddingStateLabels[state] : '等待本地服务连接'}</strong>
              <span>{runtimeStatus.embeddingMessage || runtimeStatus.message}</span>
            </div>
            <span className="model-status-name">{runtimeStatus.embeddingModelId ?? '尚未检测'}</span>
          </div>
          <div className="model-status-card" role="status">
            {runtimeStatus.rerankerState === 'error' ? <AlertTriangle size={19} /> : <CheckCircle2 size={19} />}
            <div>
              <strong>{runtimeStatus.rerankerState ? rerankerStateLabels[runtimeStatus.rerankerState] : '等待重排器状态'}</strong>
              <span>{runtimeStatus.rerankerMessage || '本地服务连接后显示实际重排策略。'}</span>
            </div>
            <span className="model-status-name">{runtimeStatus.rerankerModelId ?? '尚未检测'}</span>
          </div>
          <div className="model-status-card" role="status">
            {runtimeStatus.ocrState === 'ready' ? <ScanText size={19} /> : <AlertTriangle size={19} />}
            <div>
              <strong>{runtimeStatus.ocrState === 'ready' ? '扫描 PDF 本地 OCR 已就绪' : runtimeStatus.ocrState === 'unavailable' ? 'OCR 组件不可用' : runtimeStatus.ocrState === 'error' ? 'OCR 最近运行失败' : '等待 OCR 状态'}</strong>
              <span>{runtimeStatus.ocrMessage || '本地服务连接后显示扫描件解析能力。'}</span>
            </div>
            <span className="model-status-name">{runtimeStatus.ocrProvider === 'rapidocr' ? 'RapidOCR · CPU' : '尚未检测'}</span>
          </div>
        </div>
      </section>

      <section className="settings-section" aria-labelledby="retrieval-index-title">
        <div className="settings-section-heading">
          <h2 id="retrieval-index-title">当前索引</h2>
          <p>知域先分别召回语义向量和 SQLite FTS5 全文结果，经 RRF 融合后再由本地 Reranker 重排。</p>
        </div>
        <div className="retrieval-stat-grid">
          <div><Layers3 size={17} /><span>检索模式</span><strong>{runtimeStatus.indexMode === 'hybrid-rrf-rerank-v1' ? '混合检索 · RRF + 重排' : runtimeStatus.indexMode === 'hybrid-rrf-v1' ? '混合检索 · RRF' : '等待检测'}</strong></div>
          <div><FileStack size={17} /><span>已索引文档</span><strong>{documentCount} 份</strong></div>
          <div><Database size={17} /><span>文本片段</span><strong>{chunkCount} 个</strong></div>
          <div><Database size={17} /><span>向量维度</span><strong>{runtimeStatus.embeddingDimensions ?? '—'}</strong></div>
        </div>
        {(summaries.length > 1 || hasOutdatedIndexes) && (
          <div className="retrieval-version-notice" role="note">
            <AlertTriangle size={17} />
            <div><strong>{hasOutdatedIndexes ? '检测到需要重建的旧索引' : '检测到多个索引版本'}</strong><span>旧索引仍可参与全文检索，但不会与当前模型、维度或分块版本混合计算；请前往文档管理选择知识库并点击“重建索引”。</span></div>
          </div>
        )}
      </section>

      <section className="model-privacy-note"><ShieldCheck size={17} /><div><strong>本地检索边界</strong><span>Embedding 只允许连接本机回环地址；不可用时自动切换到轻量本地索引，文档内容不会发送到公网。</span></div></section>
    </>
  );
}
