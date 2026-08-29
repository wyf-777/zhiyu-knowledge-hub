import { Activity, AlertTriangle, Check, ChevronDown, Database, HardDrive, LoaderCircle, RefreshCw, ScanText, X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { RuntimeStatus } from '../services/RuntimeService';

interface LocalRuntimePopoverProps {
  open: boolean;
  status: RuntimeStatus;
  onToggle: () => void;
  onRefresh: () => void;
}

const phaseLabels: Record<RuntimeStatus['phase'], string> = {
  starting: '正在启动',
  running: '本地服务',
  stopped: '服务已停止',
  error: '服务异常',
  'web-preview': '浏览器预览',
};

const modelStateLabel: Record<NonNullable<RuntimeStatus['modelState']>, string> = {
  ready: '已就绪',
  'service-unavailable': '未运行 · 原文回退',
  'model-missing': '模型未下载',
  extractive: '严格原文模式',
  error: '检测失败',
};

const embeddingStateLabel: Record<NonNullable<RuntimeStatus['embeddingState']>, string> = {
  ready: '已就绪',
  fallback: '轻量回退',
  error: '不可用',
};

const rerankerStateLabel: Record<NonNullable<RuntimeStatus['rerankerState']>, string> = {
  ready: '已启用',
  disabled: '已关闭',
  error: '不可用',
};

export function LocalRuntimePopover({ open, status, onToggle, onRefresh }: LocalRuntimePopoverProps) {
  const anchorRef = useRef<HTMLDivElement>(null);
  const hasProblem = status.phase === 'error' || status.phase === 'stopped';
  const isStarting = status.phase === 'starting';
  const indexedDocuments = status.indexSummaries?.reduce((total, item) => total + item.documentCount, 0) ?? 0;
  const indexedChunks = status.indexSummaries?.reduce((total, item) => total + item.chunkCount, 0) ?? 0;

  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (event.target instanceof Node && !anchorRef.current?.contains(event.target)) onToggle();
    };
    document.addEventListener('pointerdown', closeOnOutsideClick);
    return () => document.removeEventListener('pointerdown', closeOnOutsideClick);
  }, [open, onToggle]);

  return (
    <div className="runtime-anchor" ref={anchorRef}>
      <button className="runtime-button" onClick={onToggle} aria-expanded={open}>
        {isStarting ? <LoaderCircle className="spin" size={16} /> : hasProblem ? <AlertTriangle size={16} /> : <Activity size={16} />}
        {phaseLabels[status.phase]} <ChevronDown size={15} />
      </button>
      {open && (
        <div className="runtime-popover glass">
          <div className="popover-heading">
            <span>本地运行状态</span>
            <button className="icon-button compact" onClick={onToggle} aria-label="关闭本地运行状态"><X size={16} /></button>
          </div>
          {[
            ['桌面连接', status.phase === 'web-preview' ? '浏览器预览' : status.apiRunning ? '已连接' : '未连接'],
            ['Knowledge API', status.serviceVersion ? `v${status.serviceVersion}` : '等待启动'],
            ['协议版本', status.protocolVersion ?? '等待协商'],
            ['文档数据', '未上传云端'],
          ].map(([label, value]) => (
            <div className="runtime-row" key={label}>
              <span>{label}</span>
              <span className="runtime-status">
                {hasProblem && label === '桌面连接' ? <AlertTriangle size={14} /> : <Check size={14} />}{value}
              </span>
            </div>
          ))}
          <div className="runtime-model">
            <HardDrive size={16} />
            <span>{status.modelName || '模型服务'}</span>
            <strong>{status.modelState ? modelStateLabel[status.modelState] : status.phase === 'web-preview' ? '桌面版可用' : '等待检测'}</strong>
          </div>
          {status.modelMessage && <p className="runtime-model-message">{status.modelMessage}</p>}
          <div className="runtime-model">
            {status.embeddingState === 'fallback' || status.embeddingState === 'error' ? <AlertTriangle size={16} /> : <Database size={16} />}
            <span>{status.embeddingModelId || 'Embedding 索引'}</span>
            <strong>{status.embeddingState ? embeddingStateLabel[status.embeddingState] : status.phase === 'web-preview' ? '桌面版可用' : '等待检测'}</strong>
          </div>
          {status.embeddingMessage && <p className="runtime-model-message">{status.embeddingMessage}</p>}
          <div className="runtime-model">
            {status.rerankerState === 'error' ? <AlertTriangle size={16} /> : <Database size={16} />}
            <span>{status.rerankerModelId || 'Reranker'}</span>
            <strong>{status.rerankerState ? rerankerStateLabel[status.rerankerState] : '等待检测'}</strong>
          </div>
          {status.rerankerMessage && <p className="runtime-model-message">{status.rerankerMessage}</p>}
          <div className="runtime-model">
            {status.ocrState === 'unavailable' || status.ocrState === 'error' ? <AlertTriangle size={16} /> : <ScanText size={16} />}
            <span>{status.ocrProvider === 'rapidocr' ? 'RapidOCR' : '扫描件 OCR'}</span>
            <strong>{status.ocrState === 'ready' ? '本地可用' : status.ocrState === 'unavailable' ? '组件缺失' : status.ocrState === 'error' ? '运行异常' : '等待检测'}</strong>
          </div>
          {status.ocrMessage && <p className="runtime-model-message">{status.ocrMessage}</p>}
          <div className="runtime-index-summary">
            <span>检索方式 <strong>{status.indexMode === 'hybrid-rrf-rerank-v1' ? '向量 + 全文 + 重排' : status.indexMode === 'hybrid-rrf-v1' ? '向量 + 全文混合' : status.indexMode ?? '等待检测'}</strong></span>
            <span>索引规模 <strong>{indexedDocuments} 份文档 · {indexedChunks} 个片段</strong></span>
            {status.embeddingDimensions ? <span>向量维度 <strong>{status.embeddingDimensions}</strong></span> : null}
          </div>
          <p className="runtime-message">{status.message}</p>
          <button className="runtime-simulate" onClick={onRefresh}>
            <RefreshCw size={14} /> {hasProblem ? '重启本地服务' : '重新检测'}
          </button>
        </div>
      )}
    </div>
  );
}
