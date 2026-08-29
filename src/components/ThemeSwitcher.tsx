import type { ThemeName } from '../types';
import { Check } from 'lucide-react';

interface ThemeSwitcherProps {
  value: ThemeName;
  onChange: (theme: ThemeName) => void;
}

export function ThemeSwitcher({ value, onChange }: ThemeSwitcherProps) {
  const themes: Array<{ id: ThemeName; name: string; description: string }> = [
    { id: 'black', name: '黑色', description: '沉浸、低干扰' },
    { id: 'gray', name: '灰色', description: '柔和、低对比' },
    { id: 'white', name: '白色', description: '明亮、清晰' },
  ];

  return (
    <div className="theme-switcher-settings" role="radiogroup" aria-label="应用主题">
      {themes.map((theme) => (
        <button
          key={theme.id}
          className={`theme-choice theme-choice-${theme.id} ${value === theme.id ? 'is-active' : ''}`}
          onClick={() => onChange(theme.id)}
          role="radio"
          aria-checked={value === theme.id}
          aria-label={`${theme.name}主题：${theme.description}`}
        >
          <span className="theme-preview" aria-hidden="true">
            <i className="theme-preview-sidebar" />
            <i className="theme-preview-top" />
            <i className="theme-preview-message" />
            <i className="theme-preview-composer" />
          </span>
          <span className="theme-choice-copy"><strong>{theme.name}</strong><small>{theme.description}</small></span>
          {value === theme.id && <span className="theme-choice-check"><Check size={14} /></span>}
        </button>
      ))}
    </div>
  );
}
