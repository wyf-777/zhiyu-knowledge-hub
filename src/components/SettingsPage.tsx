import { useMemo, useState } from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  ArrowLeft,
  Bot,
  Database,
  HardDrive,
  Info,
  Keyboard,
  Palette,
  Search,
  Settings2,
  ShieldCheck,
} from 'lucide-react';
import type { ThemeName } from '../types';
import type { LocalModelStatus } from '../services/KnowledgeApiService';
import type { RuntimeStatus } from '../services/RuntimeService';
import { ModelSettingsPanel } from './ModelSettingsPanel';
import { RetrievalSettingsPanel } from './RetrievalSettingsPanel';
import { StorageSettingsPanel } from './StorageSettingsPanel';
import { ThemeSwitcher } from './ThemeSwitcher';

type SettingsSectionId = 'general' | 'appearance' | 'shortcuts' | 'models' | 'retrieval' | 'storage' | 'privacy' | 'about';

interface SettingsSection {
  id: SettingsSectionId;
  label: string;
  description: string;
  icon: LucideIcon;
}

interface SettingsGroup {
  label: string;
  items: SettingsSection[];
}

const settingsGroups: SettingsGroup[] = [
  {
    label: '个人',
    items: [
      { id: 'general', label: '常规', description: '语言、启动和通知', icon: Settings2 },
      { id: 'appearance', label: '外观', description: '主题和界面显示', icon: Palette },
      { id: 'shortcuts', label: '快捷键', description: '键盘操作方式', icon: Keyboard },
    ],
  },
  {
    label: '知识库',
    items: [
      { id: 'models', label: '模型与运行', description: '本地模型和推理服务', icon: Bot },
      { id: 'retrieval', label: '检索与索引', description: '切分、Embedding、召回和重排', icon: Database },
      { id: 'storage', label: '存储', description: '文档、索引和备份位置', icon: HardDrive },
    ],
  },
  {
    label: '安全',
    items: [
      { id: 'privacy', label: '隐私与数据', description: '本地数据与云端策略', icon: ShieldCheck },
      { id: 'about', label: '关于', description: '版本和开源许可', icon: Info },
    ],
  },
];

interface SettingsPageProps {
  theme: ThemeName;
  onThemeChange: (theme: ThemeName) => void;
  onBack: () => void;
  runtimeStatus: RuntimeStatus;
  onModelStatusChange: (status: LocalModelStatus) => void;
  onDataRestored: () => void;
}

export function SettingsPage({ theme, onThemeChange, onBack, runtimeStatus, onModelStatusChange, onDataRestored }: SettingsPageProps) {
  const [activeSection, setActiveSection] = useState<SettingsSectionId>('appearance');
  const [query, setQuery] = useState('');

  const filteredGroups = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return settingsGroups;
    return settingsGroups
      .map((group) => ({
        ...group,
        items: group.items.filter((item) => `${item.label}${item.description}`.toLocaleLowerCase().includes(normalized)),
      }))
      .filter((group) => group.items.length > 0);
  }, [query]);

  const currentSection = settingsGroups.flatMap((group) => group.items).find((item) => item.id === activeSection) ?? settingsGroups[0].items[0];
  const CurrentIcon = currentSection.icon;

  return (
    <div className="settings-page">
      <aside className="settings-navigation" aria-label="设置导航" data-tauri-drag-region="">
        <button className="settings-back-button" onClick={onBack}>
          <ArrowLeft size={17} />
          <span>返回知识库</span>
        </button>

        <label className="settings-search">
          <Search size={16} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索设置..." aria-label="搜索设置" />
        </label>

        <nav className="settings-nav-groups">
          {filteredGroups.map((group) => (
            <div className="settings-nav-group" key={group.label}>
              <p>{group.label}</p>
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    className={activeSection === item.id ? 'is-active' : ''}
                    onClick={() => setActiveSection(item.id)}
                    aria-current={activeSection === item.id ? 'page' : undefined}
                  >
                    <Icon size={17} />
                    <span>{item.label}</span>
                  </button>
                );
              })}
            </div>
          ))}
          {filteredGroups.length === 0 && <p className="settings-search-empty">没有匹配的设置</p>}
        </nav>
      </aside>

      <main className="settings-workspace" data-tauri-drag-region="">
        <div className="settings-content">
          <header className="settings-heading">
            <div className="settings-heading-icon"><CurrentIcon size={20} /></div>
            <div>
              <h1>{currentSection.label}</h1>
              <p>{currentSection.description}</p>
            </div>
          </header>

          {activeSection === 'appearance' ? (
            <>
              <section className="settings-section" aria-labelledby="theme-settings-title">
                <div className="settings-section-heading">
                  <h2 id="theme-settings-title">应用主题</h2>
                  <p>主题只调整黑、白、灰的明暗、边框和阴影，不改变界面布局。</p>
                </div>
                <ThemeSwitcher value={theme} onChange={onThemeChange} />
              </section>

              <section className="settings-section" aria-labelledby="interface-settings-title">
                <div className="settings-section-heading">
                  <h2 id="interface-settings-title">界面</h2>
                  <p>下列选项将在桌面端工程接入后统一管理。</p>
                </div>
                <div className="settings-list-card">
                  <div className="settings-list-row">
                    <div><strong>界面密度</strong><span>对话、导航和设置项的默认间距</span></div>
                    <span className="settings-value">舒适</span>
                  </div>
                  <div className="settings-list-row">
                    <div><strong>侧边栏启动状态</strong><span>下次启动时恢复上一次展开或收起状态</span></div>
                    <span className="settings-value">记住上次</span>
                  </div>
                  <div className="settings-list-row">
                    <div><strong>界面缩放</strong><span>跟随 Windows 或 macOS 的系统缩放比例</span></div>
                    <span className="settings-value">跟随系统</span>
                  </div>
                </div>
              </section>
            </>
          ) : activeSection === 'models' ? (
            <ModelSettingsPanel runtimeStatus={runtimeStatus} onStatusChange={onModelStatusChange} />
          ) : activeSection === 'retrieval' ? (
            <RetrievalSettingsPanel runtimeStatus={runtimeStatus} />
          ) : activeSection === 'storage' ? (
            <StorageSettingsPanel runtimeStatus={runtimeStatus} onDataRestored={onDataRestored} />
          ) : (
            <section className="settings-placeholder">
              <CurrentIcon size={24} />
              <h2>{currentSection.label}</h2>
              <p>{currentSection.description}将在对应本地服务接入时开放。当前先确定设置的信息架构，避免加入无法生效的假开关。</p>
            </section>
          )}
        </div>
      </main>
    </div>
  );
}
