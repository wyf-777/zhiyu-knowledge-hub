import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AppShell } from './components/AppShell';
import { BackgroundTaskButton } from './components/BackgroundTaskButton';
import { BackgroundTaskPanel } from './components/BackgroundTaskPanel';
import { ChatComposer } from './components/ChatComposer';
import { ChatView } from './components/ChatView';
import { CitationDrawer } from './components/CitationDrawer';
import { DesktopWindowControls } from './components/DesktopWindowControls';
import { DocumentManagementPage } from './components/DocumentManagementPage';
import { KnowledgeSearchDialog } from './components/KnowledgeSearchDialog';
import { Sidebar } from './components/Sidebar';
import { SettingsPage } from './components/SettingsPage';
import { TopBar } from './components/TopBar';
import { initialConversations, initialMessages, initialTasks, knowledgeBases as mockKnowledgeBases } from './data/mockData';
import type { ThemeName } from './types';
import type { AnswerPerformance, ChatMessage, Citation, Conversation, KnowledgeDocument } from './types';
import { mockChatService } from './services/MockChatService';
import { runtimeService } from './services/RuntimeService';
import type { RuntimeStatus } from './services/RuntimeService';
import { knowledgeApiService } from './services/KnowledgeApiService';
import type { LocalModelStatus } from './services/KnowledgeApiService';
import { localChatService } from './services/LocalChatService';
import { isTauri } from '@tauri-apps/api/core';

function getInitialTheme(): ThemeName {
  const saved = localStorage.getItem('zhiyu-theme');
  return saved === 'white' || saved === 'gray' || saved === 'black' ? saved : 'black';
}

function readLocalValue<T>(key: string, fallback: T): T {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) as T : fallback;
  } catch {
    return fallback;
  }
}

export default function App() {
  const desktopRuntime = isTauri();
  const [theme, setTheme] = useState<ThemeName>(getInitialTheme);
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState('enterprise');
  const [knowledgeBaseItems, setKnowledgeBaseItems] = useState(mockKnowledgeBases);
  const [conversationItems, setConversationItems] = useState<Conversation[]>(() => readLocalValue('zhiyu-conversations', desktopRuntime ? [] : initialConversations));
  const [conversationMessageItems, setConversationMessageItems] = useState<Record<string, ChatMessage[]>>(() => readLocalValue('zhiyu-conversation-messages', {}));
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(desktopRuntime ? null : 'trip-standard');
  const [messages, setMessages] = useState(desktopRuntime ? [] : initialMessages);
  const [composerValue, setComposerValue] = useState('');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem('zhiyu-sidebar-collapsed') === 'true');
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [citationOpen, setCitationOpen] = useState(!desktopRuntime);
  const [activeCitationId, setActiveCitationId] = useState<number | null>(desktopRuntime ? null : 1);
  const [runtimeOpen, setRuntimeOpen] = useState(false);
  const [taskPanelOpen, setTaskPanelOpen] = useState(false);
  const [tasks, setTasks] = useState(desktopRuntime ? [] : initialTasks);
  const [citationLoading, setCitationLoading] = useState(false);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus>(() => runtimeService.previewStatus());
  const [serviceError, setServiceError] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [documentsOpen, setDocumentsOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [chatDocuments, setChatDocuments] = useState<KnowledgeDocument[]>([]);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [attachmentUploading, setAttachmentUploading] = useState(false);
  const [attachmentStatus, setAttachmentStatus] = useState('');
  const [notice, setNotice] = useState('');
  const [conversationPersistenceReady, setConversationPersistenceReady] = useState(!desktopRuntime);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const conversationHydrationStartedRef = useRef(false);

  const applyModelStatus = useCallback((model: LocalModelStatus) => {
    setRuntimeStatus((current) => ({
      ...current,
      modelProvider: model.provider,
      modelState: model.state,
      modelName: model.model,
      modelBaseUrl: model.baseUrl,
      modelMessage: model.message,
      modelExecutionMode: model.executionMode,
    }));
  }, []);

  useEffect(() => {
    localStorage.setItem('zhiyu-sidebar-collapsed', String(sidebarCollapsed));
  }, [sidebarCollapsed]);

  useEffect(() => {
    if (desktopRuntime) return;
    localStorage.setItem('zhiyu-conversations', JSON.stringify(conversationItems));
  }, [conversationItems, desktopRuntime]);

  useEffect(() => {
    if (!selectedConversationId) return;
    setConversationMessageItems((current) => ({ ...current, [selectedConversationId]: messages }));
  }, [messages, selectedConversationId]);

  useEffect(() => {
    if (desktopRuntime) return;
    const timer = window.setTimeout(() => {
      localStorage.setItem('zhiyu-conversation-messages', JSON.stringify(conversationMessageItems));
    }, 180);
    return () => window.clearTimeout(timer);
  }, [conversationMessageItems, desktopRuntime]);

  useEffect(() => {
    if (!desktopRuntime || runtimeStatus.phase !== 'running' || conversationPersistenceReady || conversationHydrationStartedRef.current) return;
    conversationHydrationStartedRef.current = true;
    let active = true;
    const hydrate = async () => {
      try {
        const legacyConversations = readLocalValue<Conversation[]>('zhiyu-conversations', []);
        const legacyMessages = readLocalValue<Record<string, ChatMessage[]>>('zhiyu-conversation-messages', {});
        if (legacyConversations.length > 0) {
          await knowledgeApiService.importLegacyConversations(runtimeStatus, legacyConversations, legacyMessages);
        }
        const persisted = await knowledgeApiService.listConversations(runtimeStatus);
        if (!active) return;
        setConversationItems(persisted.conversations);
        setConversationMessageItems(persisted.messages);
        const first = persisted.conversations.find((item) => item.knowledgeBaseId === selectedKnowledgeBaseId);
        setSelectedConversationId(first?.id ?? null);
        setMessages(first ? persisted.messages[first.id] ?? [] : []);
        setConversationPersistenceReady(true);
        localStorage.setItem('zhiyu-sqlite-conversation-migration-v1', JSON.stringify({ completedAt: new Date().toISOString(), imported: legacyConversations.length }));
      } catch (error) {
        conversationHydrationStartedRef.current = false;
        if (active) setNotice(error instanceof Error ? `历史对话加载失败：${error.message}` : '历史对话加载失败');
      }
    };
    void hydrate();
    return () => { active = false; };
  }, [conversationPersistenceReady, desktopRuntime, runtimeStatus.phase, runtimeStatus.baseUrl, runtimeStatus.token]);

  useEffect(() => {
    if (!desktopRuntime || !conversationPersistenceReady || runtimeStatus.phase !== 'running' || !selectedConversationId) return;
    const persist = () => {
      void knowledgeApiService.replaceConversationMessages(runtimeStatus, selectedConversationId, messages).catch((error) => {
        setNotice(error instanceof Error ? `对话保存失败：${error.message}` : '对话保存失败');
      });
    };
    if (!isGenerating) {
      persist();
      return;
    }
    const timer = window.setTimeout(persist, 650);
    return () => window.clearTimeout(timer);
  }, [conversationPersistenceReady, desktopRuntime, isGenerating, messages, runtimeStatus.phase, runtimeStatus.baseUrl, runtimeStatus.token, selectedConversationId]);

  useEffect(() => {
    const closeTransientLayers = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setCitationOpen(false);
      setRuntimeOpen(false);
      setTaskPanelOpen(false);
      setMobileSidebarOpen(false);
      setSearchOpen(false);
    };
    window.addEventListener('keydown', closeTransientLayers);
    return () => window.removeEventListener('keydown', closeTransientLayers);
  }, []);

  useEffect(() => {
    const openSearch = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'k') {
        event.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener('keydown', openSearch);
    return () => window.removeEventListener('keydown', openSearch);
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(''), 2600);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const next = await runtimeService.getStatus();
        let resolved = next;
        if (next.phase === 'running') {
          try {
            resolved = { ...next, ...await knowledgeApiService.getRuntimeDetails(next) };
          } catch {
            // The desktop service status remains useful if model detection is temporarily unavailable.
          }
        }
        if (active) setRuntimeStatus(resolved);
      } catch (error) {
        if (active) setRuntimeStatus({
          phase: 'error',
          apiRunning: false,
          baseUrl: null,
          token: null,
          serviceVersion: null,
          protocolVersion: null,
          message: error instanceof Error ? error.message : '无法读取本地服务状态',
        });
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const refreshKnowledgeData = useCallback(async () => {
    if (runtimeStatus.phase !== 'running') return;
    try {
      const [nextKnowledgeBases, nextTasks, nextDocuments] = await Promise.all([
        knowledgeApiService.listKnowledgeBases(runtimeStatus),
        knowledgeApiService.listTasks(runtimeStatus),
        knowledgeApiService.listDocuments(runtimeStatus, selectedKnowledgeBaseId),
      ]);
      setKnowledgeBaseItems(nextKnowledgeBases);
      setTasks(nextTasks);
      setChatDocuments(nextDocuments);
      const availableDocumentIds = new Set(nextDocuments.filter((item) => item.status === 'ready').map((item) => item.id));
      setSelectedDocumentIds((current) => current.filter((id) => availableDocumentIds.has(id)));
    } catch {
      // The runtime status poll owns the visible service error state.
    }
  }, [runtimeStatus, selectedKnowledgeBaseId]);

  useEffect(() => {
    if (runtimeStatus.phase !== 'running') return;
    void refreshKnowledgeData();
    const timer = window.setInterval(() => void refreshKnowledgeData(), 1800);
    return () => window.clearInterval(timer);
  }, [refreshKnowledgeData, runtimeStatus.phase]);

  const selectedKnowledgeBase = knowledgeBaseItems.find((item) => item.id === selectedKnowledgeBaseId) ?? knowledgeBaseItems[0] ?? mockKnowledgeBases[0];
  const conversations = useMemo(
    () => conversationItems.filter((item) => item.knowledgeBaseId === selectedKnowledgeBaseId).sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned))),
    [conversationItems, selectedKnowledgeBaseId],
  );
  const currentCitations = useMemo(
    () => [...messages].reverse().find((message) => message.role === 'assistant')?.citations ?? [],
    [messages],
  );

  const changeTheme = (nextTheme: ThemeName) => {
    setTheme(nextTheme);
    localStorage.setItem('zhiyu-theme', nextTheme);
  };

  const refreshRuntime = async () => {
    try {
      const next = runtimeStatus.phase === 'error' || runtimeStatus.phase === 'stopped'
        ? await runtimeService.restart()
        : await runtimeService.getStatus();
      let resolved = next;
      if (next.phase === 'running') {
        try {
          resolved = { ...next, ...await knowledgeApiService.getRuntimeDetails(next) };
        } catch {
          // Keep the core runtime connected and surface model fallback separately.
        }
      }
      setRuntimeStatus(resolved);
      if (next.phase === 'running' || next.phase === 'web-preview') setServiceError(false);
    } catch (error) {
      setRuntimeStatus((current) => ({
        ...current,
        phase: 'error',
        apiRunning: false,
        message: error instanceof Error ? error.message : '本地服务重启失败',
      }));
    }
  };

  const startNewConversation = () => {
    setSelectedConversationId(null);
    setMessages([]);
    setComposerValue('');
    setCitationOpen(false);
    setActiveCitationId(null);
    setSelectedDocumentIds([]);
    requestAnimationFrame(() => composerRef.current?.focus());
  };

  const selectKnowledgeBase = (id: string) => {
    setSelectedKnowledgeBaseId(id);
    const first = conversationItems.find((item) => item.knowledgeBaseId === id);
    setSelectedConversationId(first?.id ?? null);
    setMessages(first ? conversationMessageItems[first.id] ?? (!desktopRuntime ? initialMessages : []) : []);
    setSelectedDocumentIds([]);
    setMobileSidebarOpen(false);
  };

  const selectConversation = (id: string) => {
    if (selectedConversationId !== id) {
      setSelectedConversationId(id);
      setMessages(conversationMessageItems[id] ?? (!desktopRuntime ? initialMessages : []));
    }
    setMobileSidebarOpen(false);
  };

  const openSettings = () => {
    setRuntimeOpen(false);
    setTaskPanelOpen(false);
    setCitationOpen(false);
    setMobileSidebarOpen(false);
    setDocumentsOpen(false);
    setSearchOpen(false);
    setSettingsOpen(true);
  };

  const openDocumentManager = () => {
    setRuntimeOpen(false);
    setTaskPanelOpen(false);
    setCitationOpen(false);
    setMobileSidebarOpen(false);
    setSettingsOpen(false);
    setSearchOpen(false);
    setDocumentsOpen(true);
  };

  const openCitation = (id: number) => {
    setCitationOpen(true);
    setActiveCitationId(id);
    setCitationLoading(true);
    window.setTimeout(() => {
      setCitationLoading(false);
      requestAnimationFrame(() => document.getElementById(`citation-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
    }, 320);
  };

  const stopGeneration = () => {
    abortControllerRef.current?.abort();
    setIsGenerating(false);
    setMessages((current) => current.map((message) => message.status === 'streaming' ? { ...message, status: 'stopped' } : message));
  };

  const sendQuestion = async (forcedQuestion?: string, appendUserMessage = true) => {
    const question = (forcedQuestion ?? composerValue).trim();
    if (!question || isGenerating) return;
    const canAnswer = desktopRuntime ? runtimeStatus.phase === 'running' : runtimeStatus.phase === 'web-preview' || runtimeStatus.phase === 'running';
    if (!canAnswer) {
      setServiceError(true);
      return;
    }
    if (desktopRuntime && !conversationPersistenceReady) {
      setNotice('正在加载本地历史对话，请稍后再发送');
      return;
    }
    setServiceError(false);
    setComposerValue('');
    setIsGenerating(true);
    const now = new Date();
    const time = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
    const userMessage: ChatMessage = { id: `user-${Date.now()}`, role: 'user', content: question, time, status: 'complete' };
    const assistantId = `assistant-${Date.now()}`;
    const assistantMessage: ChatMessage = { id: assistantId, role: 'assistant', content: '', time, status: 'streaming' };
    let historySource = messages.filter((message) => message.content.trim() && message.status !== 'error' && message.status !== 'streaming');
    if (!appendUserMessage) {
      let lastUserIndex = -1;
      for (let index = historySource.length - 1; index >= 0; index -= 1) {
        if (historySource[index].role === 'user') {
          lastUserIndex = index;
          break;
        }
      }
      historySource = lastUserIndex >= 0 ? historySource.slice(0, lastUserIndex) : historySource;
    }
    const history = historySource.slice(-10).map(({ role, content }) => ({ role, content }));
    let conversationId = selectedConversationId;
    if (appendUserMessage && !selectedConversationId) {
      const id = `conversation-${Date.now()}`;
      const conversation: Conversation = { id, knowledgeBaseId: selectedKnowledgeBaseId, title: question, updatedAt: time };
      setConversationItems((current) => [conversation, ...current]);
      setSelectedConversationId(id);
      conversationId = id;
      if (desktopRuntime) {
        try {
          await knowledgeApiService.createConversation(runtimeStatus, conversation);
        } catch (error) {
          setConversationItems((current) => current.filter((item) => item.id !== id));
          setSelectedConversationId(null);
          setComposerValue(question);
          setIsGenerating(false);
          setNotice(error instanceof Error ? `无法创建本地对话：${error.message}` : '无法创建本地对话');
          return;
        }
      }
    }
    setMessages((current) => [...current, ...(appendUserMessage ? [userMessage] : []), assistantMessage]);

    const controller = new AbortController();
    abortControllerRef.current = controller;
    try {
      const result = desktopRuntime
        ? await localChatService.streamAnswer(question, selectedKnowledgeBaseId, runtimeStatus, selectedDocumentIds, history, conversationId, controller.signal, (chunk) => {
          setMessages((current) => current.map((message) => message.id === assistantId ? { ...message, content: message.content + chunk } : message));
        })
        : await mockChatService.streamAnswer(question, controller.signal, (chunk) => {
        setMessages((current) => current.map((message) => message.id === assistantId ? { ...message, content: message.content + chunk } : message));
      });
      const performance = 'performance' in result ? result.performance as AnswerPerformance : undefined;
      setMessages((current) => current.map((message) => message.id === assistantId ? {
        ...message,
        citations: result.citations,
        suggestions: result.suggestions,
        status: result.noEvidence ? 'no-evidence' : 'complete',
        performance,
      } : message));
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        setServiceError(true);
        setMessages((current) => current.map((message) => message.id === assistantId ? { ...message, status: 'error' } : message));
      }
    } finally {
      setIsGenerating(false);
      abortControllerRef.current = null;
    }
  };

  const regenerate = () => {
    const lastUser = [...messages].reverse().find((message) => message.role === 'user');
    if (!lastUser) return;
    setMessages((current) => current.filter((message) => message.id !== [...current].reverse().find((entry) => entry.role === 'assistant')?.id));
    void sendQuestion(lastUser.content, false);
  };

  const handleSuggestion = (text: string) => {
    if (text === '查看已上传文档') {
      openDocumentManager();
      return;
    }
    if (text === '换一种方式提问') {
      const lastQuestion = [...messages].reverse().find((message) => message.role === 'user')?.content ?? '';
      setComposerValue(lastQuestion);
      setNotice('原问题已放回输入框，可补充对象、条件或文档范围后再发送');
      requestAnimationFrame(() => composerRef.current?.focus());
      return;
    }
    void sendQuestion(text);
  };

  const uploadChatAttachments = async (files: File[]) => {
    if (runtimeStatus.phase !== 'running' || attachmentUploading) return;
    setAttachmentUploading(true);
    setAttachmentStatus('正在加入本地索引…');
    try {
      await knowledgeApiService.uploadDocuments(runtimeStatus, selectedKnowledgeBaseId, files);
      await refreshKnowledgeData();
      setAttachmentStatus(`已添加 ${files.length} 份文档，后台正在索引`);
      setNotice('附件已加入当前知识库');
    } catch (error) {
      setAttachmentStatus(error instanceof Error ? error.message : '附件上传失败');
    } finally {
      setAttachmentUploading(false);
    }
  };

  const openCitationDocument = async (citation: Citation) => {
    if (!citation.documentId) throw new Error('当前来源没有可打开的本地文件。');
    await knowledgeApiService.openDocument(runtimeStatus, citation.documentId);
    setNotice(`已使用系统默认应用打开 ${citation.documentName}`);
  };

  const previewCitationDocument = async (citation: Citation) => {
    if (!citation.documentId || !citation.chunkId) throw new Error('当前来源没有可定位的本地引用。');
    return knowledgeApiService.getCitationPreview(runtimeStatus, citation.documentId, citation.chunkId);
  };

  const copyAnswer = async (message: ChatMessage) => {
    try {
      await navigator.clipboard.writeText(message.content);
      setNotice('回答已复制');
    } catch {
      setNotice('复制失败，请手动选择文本');
    }
  };

  const retryBackgroundTask = async (taskId: string) => {
    const task = tasks.find((item) => item.id === taskId);
    if (runtimeStatus.phase === 'running' && task?.documentId) {
      try {
        await knowledgeApiService.retryDocument(runtimeStatus, task.documentId);
        await refreshKnowledgeData();
      } catch {
        setServiceError(true);
      }
      return;
    }
    setTasks((current) => current.map((item) => item.id === taskId ? {
      ...item,
      progress: 32,
      stage: '正在重新解析文档',
      status: 'running',
      steps: { ...item.steps, ocr: 'running' },
    } : item));
  };

  const renameConversation = async (id: string, title: string) => {
    const previous = conversationItems.find((item) => item.id === id);
    setConversationItems((current) => current.map((item) => item.id === id ? { ...item, title } : item));
    if (!desktopRuntime || runtimeStatus.phase !== 'running') return;
    try {
      await knowledgeApiService.updateConversation(runtimeStatus, id, { title });
    } catch (error) {
      if (previous) setConversationItems((current) => current.map((item) => item.id === id ? previous : item));
      setNotice(error instanceof Error ? `重命名失败：${error.message}` : '重命名失败');
    }
  };

  const toggleConversationPin = async (id: string) => {
    const previous = conversationItems.find((item) => item.id === id);
    if (!previous) return;
    const pinned = !previous.pinned;
    setConversationItems((current) => current.map((item) => item.id === id ? { ...item, pinned } : item));
    if (!desktopRuntime || runtimeStatus.phase !== 'running') return;
    try {
      await knowledgeApiService.updateConversation(runtimeStatus, id, { pinned });
    } catch (error) {
      setConversationItems((current) => current.map((item) => item.id === id ? previous : item));
      setNotice(error instanceof Error ? `置顶状态保存失败：${error.message}` : '置顶状态保存失败');
    }
  };

  const deleteConversation = async (id: string) => {
    const target = conversationItems.find((item) => item.id === id);
    if (!target || !window.confirm(`确定删除对话“${target.title}”吗？此操作只删除聊天记录，不会删除知识库文档。`)) return;
    if (desktopRuntime && runtimeStatus.phase === 'running') {
      try {
        await knowledgeApiService.deleteConversation(runtimeStatus, id);
      } catch (error) {
        setNotice(error instanceof Error ? `删除失败：${error.message}` : '删除失败');
        return;
      }
    }
    setConversationItems((current) => current.filter((item) => item.id !== id));
    setConversationMessageItems((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    if (selectedConversationId === id) startNewConversation();
  };

  const activeTasks = tasks.filter((task) => task.status === 'running' || task.status === 'waiting');
  const taskProgress = activeTasks.length
    ? Math.round(activeTasks.reduce((total, task) => total + task.progress, 0) / activeTasks.length)
    : tasks.length ? Math.round(tasks.reduce((total, task) => total + task.progress, 0) / tasks.length) : 0;
  const chatCitationOpen = !settingsOpen && !documentsOpen && !searchOpen && citationOpen;

  return (
    <div
      className="app-root"
      data-theme={theme}
      data-page={settingsOpen ? 'settings' : documentsOpen ? 'documents' : 'chat'}
      data-desktop-window={desktopRuntime ? 'true' : undefined}
    >
      {desktopRuntime && (
        <>
          <div className="desktop-window-drag-strip" data-tauri-drag-region="" aria-hidden="true" />
          <DesktopWindowControls />
        </>
      )}
      {settingsOpen ? (
        <SettingsPage
          theme={theme}
          onThemeChange={changeTheme}
          onBack={() => setSettingsOpen(false)}
          runtimeStatus={runtimeStatus}
          onModelStatusChange={applyModelStatus}
          onDataRestored={() => window.location.reload()}
        />
      ) : documentsOpen ? (
        <DocumentManagementPage
          runtimeStatus={runtimeStatus}
          knowledgeBases={knowledgeBaseItems}
          initialKnowledgeBaseId={selectedKnowledgeBaseId}
          onBack={() => setDocumentsOpen(false)}
          onSelectKnowledgeBase={setSelectedKnowledgeBaseId}
          onDataChanged={refreshKnowledgeData}
        />
      ) : (
      <AppShell
        sidebarCollapsed={sidebarCollapsed}
        citationOpen={citationOpen}
        sidebar={
          <Sidebar
            knowledgeBases={knowledgeBaseItems}
            conversations={conversations}
            selectedKnowledgeBaseId={selectedKnowledgeBaseId}
            selectedConversationId={selectedConversationId}
            collapsed={sidebarCollapsed}
            mobileOpen={mobileSidebarOpen}
            onToggle={() => setSidebarCollapsed((value) => !value)}
            onCloseMobile={() => setMobileSidebarOpen(false)}
            onNewConversation={startNewConversation}
            onSelectKnowledgeBase={selectKnowledgeBase}
            onSelectConversation={selectConversation}
            onRenameConversation={(id, title) => void renameConversation(id, title)}
            onPinConversation={(id) => void toggleConversationPin(id)}
            onDeleteConversation={(id) => void deleteConversation(id)}
            onOpenSettings={openSettings}
            onOpenDocumentManager={openDocumentManager}
          />
        }
        topBar={
          <TopBar
            knowledgeBase={selectedKnowledgeBase}
            knowledgeBases={knowledgeBaseItems}
            runtimeStatus={runtimeStatus}
            runtimeOpen={runtimeOpen}
            sidebarCollapsed={sidebarCollapsed}
            onRuntimeToggle={() => setRuntimeOpen((value) => !value)}
            onRuntimeRefresh={() => void refreshRuntime()}
            onOpenMobileSidebar={() => { setSidebarCollapsed(false); setMobileSidebarOpen(true); }}
            onExpandSidebar={() => setSidebarCollapsed(false)}
            onCitationToggle={() => setCitationOpen((value) => !value)}
            onSelectKnowledgeBase={selectKnowledgeBase}
            onSearch={() => setSearchOpen(true)}
            onNewConversation={startNewConversation}
            onOpenDocuments={openDocumentManager}
            onOpenSettings={openSettings}
          />
        }
        citationDrawer={
          <CitationDrawer
            open={citationOpen}
            items={currentCitations}
            activeId={activeCitationId}
            loading={citationLoading}
            onClose={() => setCitationOpen(false)}
            onSelect={setActiveCitationId}
            onOpenDocument={openCitationDocument}
            onPreviewDocument={previewCitationDocument}
          />
        }
      >
        <ChatView
          messages={messages}
          knowledgeBaseName={selectedKnowledgeBase.name}
          onOpenCitation={openCitation}
          serviceError={serviceError}
          onRetryService={() => void refreshRuntime()}
          onCopy={(message) => void copyAnswer(message)}
          onRegenerate={regenerate}
          onFeedback={(id, feedback) => setMessages((current) => current.map((message) => message.id === id ? { ...message, feedback } : message))}
          onSuggestion={handleSuggestion}
        />
        <ChatComposer
          value={composerValue}
          knowledgeBaseName={selectedKnowledgeBase.name}
          onChange={setComposerValue}
          onSend={() => void sendQuestion()}
          onStop={stopGeneration}
          isGenerating={isGenerating}
          textareaRef={composerRef}
          documents={chatDocuments}
          selectedDocumentIds={selectedDocumentIds}
          canManageDocuments={runtimeStatus.phase === 'running'}
          attachmentUploading={attachmentUploading}
          attachmentStatus={attachmentStatus}
          onSelectedDocumentsChange={setSelectedDocumentIds}
          onAttachFiles={(files) => void uploadChatAttachments(files)}
        />
        </AppShell>
      )}
      <BackgroundTaskPanel
        open={taskPanelOpen}
        tasks={tasks}
        citationOpen={chatCitationOpen}
        onClose={() => setTaskPanelOpen(false)}
        onRetry={(id) => void retryBackgroundTask(id)}
      />
      <BackgroundTaskButton
        count={tasks.length}
        progress={taskProgress}
        open={taskPanelOpen}
        citationOpen={chatCitationOpen}
        onClick={() => setTaskPanelOpen((value) => !value)}
      />
      <KnowledgeSearchDialog
        open={searchOpen}
        runtimeStatus={runtimeStatus}
        knowledgeBase={selectedKnowledgeBase}
        conversations={conversations}
        messages={messages}
        onClose={() => setSearchOpen(false)}
        onSelectConversation={selectConversation}
      />
      {notice && <div className="app-notice" role="status">{notice}</div>}
    </div>
  );
}
