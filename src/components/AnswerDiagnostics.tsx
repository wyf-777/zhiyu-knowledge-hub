import { Activity, Cpu, Database, Gauge, ShieldCheck } from 'lucide-react';
import type { AnswerPerformance } from '../types';

interface AnswerDiagnosticsProps {
  performance: AnswerPerformance;
}

function milliseconds(value?: number | null) {
  if (value === undefined || value === null) return '—';
  return value >= 1000 ? `${(value / 1000).toFixed(2)} 秒` : `${value} 毫秒`;
}

const pathLabels: Record<NonNullable<AnswerPerformance['answerPath']>, string> = {
  generated: 'Qwen 生成回答',
  'extractive-fast': '快速原文抽取',
  'extractive-fallback': '原文安全回退',
  'no-evidence': '无可靠依据拒答',
};

const contextLabels = {
  independent: '独立问题',
  follow_up: '继承上下文',
  uncertain: '模糊追问判定',
} as const;

export function AnswerDiagnostics({ performance }: AnswerDiagnosticsProps) {
  const timings = performance.timingsMs;
  const retryCount = performance.validationRetryCount ?? 0;
  const answerPath = performance.answerPath ? pathLabels[performance.answerPath] : performance.provider;
  return (
    <section className="answer-diagnostics" aria-label="本次回答性能诊断">
      <header>
        <div><Gauge size={16} /><strong>本次回答诊断</strong></div>
        <span>仅保存在本机当前会话</span>
      </header>
      <div className="answer-diagnostic-grid">
        <div><Activity size={15} /><span>总耗时</span><strong>{milliseconds(timings.total)}</strong></div>
        <div><Gauge size={15} /><span>首段可见</span><strong>{milliseconds(timings.firstAnswerDelta)}</strong></div>
        <div><Database size={15} /><span>问题规划</span><strong>{milliseconds(timings.planning)}</strong></div>
        <div><Database size={15} /><span>检索与重排</span><strong>{milliseconds(timings.retrieval)}</strong></div>
        <div><Cpu size={15} /><span>回答生成</span><strong>{milliseconds(timings.generation)}</strong></div>
        <div><Cpu size={15} /><span>生成速度</span><strong>{performance.tokensPerSecond ? `${performance.tokensPerSecond} tokens/s` : '不适用'}</strong></div>
      </div>
      <dl className="answer-diagnostic-details">
        <div><dt>回答路径</dt><dd>{answerPath}</dd></div>
        <div><dt>上下文判定</dt><dd>{performance.queryContext ? contextLabels[performance.queryContext] : '—'}{performance.queryRewritten ? ' · 已改写检索问题' : ''}</dd></div>
        <div><dt>检索规模</dt><dd>{performance.retrievalQueryCount ?? '—'} 路查询 · {performance.retrievalCandidateCount ?? '—'} 个候选 · {performance.citationCount ?? '—'} 个引用</dd></div>
        <div><dt>Embedding</dt><dd>{performance.embeddingModel ?? '—'} · {performance.embeddingExecutionMode ?? '—'}</dd></div>
        <div><dt>模型统计</dt><dd>{performance.modelLoadMs !== undefined ? `加载 ${milliseconds(performance.modelLoadMs)} · ` : ''}{performance.promptTokens ?? '—'} 输入 tokens · {performance.generatedTokens ?? '—'} 输出 tokens</dd></div>
        <div><dt>安全校验</dt><dd>{retryCount ? `触发 ${retryCount} 次引用修复重试` : '一次通过'}{performance.usedExtractiveFallback ? ' · 已回退原文' : ''}{performance.modelCpuFallback ? ' · 模型使用 CPU 回退' : ''}</dd></div>
      </dl>
      <p><ShieldCheck size={14} />诊断数据不包含问题正文或文档内容，不会上传云端。</p>
    </section>
  );
}
