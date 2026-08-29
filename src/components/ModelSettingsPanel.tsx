import { AlertTriangle, CheckCircle2, LoaderCircle, RefreshCw, Save, Server, ShieldCheck } from 'lucide-react';
import { useEffect, useState } from 'react';
import type { LocalModelSettings, LocalModelStatus } from '../services/KnowledgeApiService';
import { knowledgeApiService } from '../services/KnowledgeApiService';
import type { ModelProvider, RuntimeStatus } from '../services/RuntimeService';

interface ModelSettingsPanelProps {
  runtimeStatus: RuntimeStatus;
  onStatusChange: (status: LocalModelStatus) => void;
}

const defaults: Record<Exclude<ModelProvider, 'extractive'>, Pick<LocalModelSettings, 'baseUrl' | 'model'>> = {
  auto: { baseUrl: 'http://127.0.0.1:11434', model: 'qwen3:8b' },
  ollama: { baseUrl: 'http://127.0.0.1:11434', model: 'qwen3:8b' },
  'openai-compatible': { baseUrl: 'http://127.0.0.1:8080', model: 'local-model' },
};

const initialSettings: LocalModelSettings = { provider: 'auto', ...defaults.auto, timeoutSeconds: 90 };

const stateLabels: Record<LocalModelStatus['state'], string> = {
  ready: '本地模型已就绪',
  'service-unavailable': '模型服务未运行',
  'model-missing': '尚未下载所选模型',
  extractive: '严格原文模式',
  error: '检测失败',
};

export function ModelSettingsPanel({ runtimeStatus, onStatusChange }: ModelSettingsPanelProps) {
  const [settings, setSettings] = useState<LocalModelSettings>(initialSettings);
  const [modelStatus, setModelStatus] = useState<LocalModelStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const desktopReady = runtimeStatus.phase === 'running';

  useEffect(() => {
    if (!desktopReady) return;
    let active = true;
    setBusy(true);
    setError('');
    knowledgeApiService.getModelSettings(runtimeStatus)
      .then((result) => {
        if (!active) return;
        setSettings(result.settings);
        setModelStatus(result.status);
        onStatusChange(result.status);
      })
      .catch((reason) => active && setError(reason instanceof Error ? reason.message : '无法读取模型设置'))
      .finally(() => active && setBusy(false));
    return () => { active = false; };
  }, [desktopReady, runtimeStatus.baseUrl, runtimeStatus.token]);

  const updateProvider = (provider: ModelProvider) => {
    setSettings((current) => provider === 'extractive'
      ? { ...current, provider }
      : { ...current, provider, ...defaults[provider] });
  };

  const save = async () => {
    if (!desktopReady || busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await knowledgeApiService.updateModelSettings(runtimeStatus, settings);
      setSettings(result.settings);
      setModelStatus(result.status);
      onStatusChange(result.status);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '模型设置保存失败');
    } finally {
      setBusy(false);
    }
  };

  const detect = async () => {
    if (!desktopReady || busy) return;
    setBusy(true);
    setError('');
    try {
      const next = await knowledgeApiService.detectModel(runtimeStatus);
      setModelStatus(next);
      onStatusChange(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '模型服务检测失败');
    } finally {
      setBusy(false);
    }
  };

  const StatusIcon = modelStatus?.state === 'ready' || modelStatus?.state === 'extractive' ? CheckCircle2 : AlertTriangle;

  return (
    <>
      <section className="settings-section" aria-labelledby="model-provider-title">
        <div className="settings-section-heading">
          <h2 id="model-provider-title">回答模型</h2>
          <p>仅连接本机回环地址。模型不可用时自动回退到严格原文模式，检索与引用仍可继续工作。</p>
        </div>

        {!desktopReady && (
          <div className="model-inline-notice"><Server size={17} /><div><strong>请在桌面版中配置本地模型</strong><span>浏览器预览不会连接本机知识服务。</span></div></div>
        )}

        <div className="model-settings-card" aria-disabled={!desktopReady}>
          <label className="model-field">
            <span>提供器</span>
            <select value={settings.provider} onChange={(event) => updateProvider(event.target.value as ModelProvider)} disabled={!desktopReady || busy}>
              <option value="auto">自动（优先 Ollama）</option>
              <option value="ollama">Ollama</option>
              <option value="openai-compatible">llama.cpp / OpenAI 兼容</option>
              <option value="extractive">严格原文模式</option>
            </select>
          </label>
          <label className="model-field">
            <span>本地服务地址</span>
            <input value={settings.baseUrl} onChange={(event) => setSettings((current) => ({ ...current, baseUrl: event.target.value }))} disabled={!desktopReady || busy || settings.provider === 'extractive'} spellCheck={false} />
            <small>仅允许 localhost、127.0.0.1 或 ::1，并且必须包含端口。</small>
          </label>
          <div className="model-settings-grid">
            <label className="model-field">
              <span>模型名称</span>
              <input value={settings.model} onChange={(event) => setSettings((current) => ({ ...current, model: event.target.value }))} disabled={!desktopReady || busy || settings.provider === 'extractive'} spellCheck={false} />
            </label>
            <label className="model-field">
              <span>回答超时</span>
              <div className="model-timeout-input"><input type="number" min="10" max="300" value={settings.timeoutSeconds} onChange={(event) => setSettings((current) => ({ ...current, timeoutSeconds: Number(event.target.value) }))} disabled={!desktopReady || busy || settings.provider === 'extractive'} /><span>秒</span></div>
            </label>
          </div>
          <div className="model-settings-actions">
            <button onClick={() => void detect()} disabled={!desktopReady || busy}><RefreshCw size={15} className={busy ? 'spin' : ''} />重新检测</button>
            <button className="is-primary" onClick={() => void save()} disabled={!desktopReady || busy}><Save size={15} />保存设置</button>
          </div>
        </div>
      </section>

      <section className="settings-section" aria-labelledby="model-status-title">
        <div className="settings-section-heading"><h2 id="model-status-title">运行状态</h2></div>
        <div className="model-status-card" role="status">
          {busy ? <LoaderCircle className="spin" size={19} /> : <StatusIcon size={19} />}
          <div>
            <strong>{busy ? '正在检测本地模型' : modelStatus ? stateLabels[modelStatus.state] : '等待检测'}</strong>
            <span>{error || modelStatus?.message || runtimeStatus.modelMessage || '保存配置后可检测模型服务。'}</span>
          </div>
          <span className="model-status-name">{modelStatus?.model ?? settings.model}{(modelStatus?.executionMode ?? runtimeStatus.modelExecutionMode) === 'cpu-compatibility' ? ' · CPU' : ''}</span>
        </div>
      </section>

      <section className="model-privacy-note"><ShieldCheck size={17} /><div><strong>本地数据边界</strong><span>检索片段只发送到你配置的本机端口。知域不会接受公网模型地址，也不会自动下载或上传文档。</span></div></section>
    </>
  );
}
