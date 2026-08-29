import type { BackgroundTask, ChatMessage, Citation, Conversation, KnowledgeBase } from '../types';

export const knowledgeBases: KnowledgeBase[] = [
  { id: 'enterprise', name: '企业制度库', documentCount: 126, updatedLabel: '今天 10:24' },
  { id: 'project', name: '项目资料', documentCount: 89, updatedLabel: '昨天 18:32' },
  { id: 'personal', name: '我的文档', documentCount: 32, updatedLabel: '8 月 27 日' },
];

export const initialConversations: Conversation[] = [
  { id: 'trip-standard', knowledgeBaseId: 'enterprise', title: '出差到上海住宿标准是多少？', pinned: true, updatedAt: '10:30' },
  { id: 'purchase-flow', knowledgeBaseId: 'enterprise', title: '采购审批流程是怎样的', updatedAt: '昨天' },
  { id: 'leave-policy', knowledgeBaseId: 'enterprise', title: '员工休假制度有哪些规定和申请条件', updatedAt: '周一' },
  { id: 'expense-docs', knowledgeBaseId: 'enterprise', title: '报销凭证有哪些要求', updatedAt: '周一' },
  { id: 'overtime-flow', knowledgeBaseId: 'enterprise', title: '加班申请流程', updatedAt: '8 月 22 日' },
  { id: 'travel-limit', knowledgeBaseId: 'enterprise', title: '差旅费用报销时效', updatedAt: '8 月 20 日' },
  { id: 'contract-sign', knowledgeBaseId: 'enterprise', title: '合同签订审批流程', updatedAt: '8 月 18 日' },
  { id: 'project-archive', knowledgeBaseId: 'project', title: '项目验收需要提交哪些材料', pinned: true, updatedAt: '昨天' },
  { id: 'project-risk', knowledgeBaseId: 'project', title: '风险登记表应该如何维护', updatedAt: '周二' },
  { id: 'notes-summary', knowledgeBaseId: 'personal', title: '总结我的产品调研笔记', updatedAt: '8 月 26 日' },
];

export const citations: Citation[] = [
  {
    id: 1,
    documentName: '员工差旅管理制度（2026版）.pdf',
    format: 'PDF',
    page: '第 8 页',
    chapter: '第三章 住宿标准',
    updatedAt: '2026-01-15 09:30',
    excerpt: '上海、北京、深圳等一类城市，普通员工住宿费标准上限为 500 元/人/晚，部门负责人上限为 650 元/人/晚。',
    context: '一类城市住宿标准适用于因公出差期间。超出标准部分原则上由个人承担，特殊情况需提前说明并完成审批。',
  },
  {
    id: 2,
    documentName: '差旅报销管理细则.docx',
    format: 'DOCX',
    page: '第 3 页',
    chapter: '第二章 报销规则',
    updatedAt: '2026-02-20 14:22',
    excerpt: '超出住宿标准的部分，原则上由个人承担。因特殊情况超标的，需提前申请并说明原因，批准后可按公司相关规定执行。',
    context: '报销申请应在差旅结束后 5 个工作日内提交，附住宿发票、行程单以及审批记录。',
  },
];

export const initialMessages: ChatMessage[] = [
  {
    id: 'user-1',
    role: 'user',
    content: '出差到上海，一类城市的住宿标准是多少？',
    time: '10:30',
    status: 'complete',
  },
  {
    id: 'assistant-1',
    role: 'assistant',
    content:
      '根据《员工差旅管理制度（2026版）》，上海属于一类城市。普通员工住宿上限为 **500 元/晚**，部门负责人为 **650 元/晚**，超标部分原则上由个人承担。\n\n具体说明如下：\n\n- 普通员工：住宿费标准上限为 500 元/人/晚；\n- 部门负责人：住宿费标准上限为 650 元/人/晚；\n- 超出标准部分：原则上由个人承担，特殊情况需提前申请。\n\n如需报销，请在出差结束后 5 个工作日内提交报销申请，并附上住宿发票和行程单。',
    time: '10:30',
    citations,
    suggestions: ['查看完整报销流程', '不同职级标准对比', '超标住宿如何申请'],
    status: 'complete',
  },
];

export const initialTasks: BackgroundTask[] = [
  {
    id: 'task-1',
    fileName: '2026员工手册.pdf',
    progress: 65,
    stage: '正在生成向量索引',
    status: 'running',
    steps: { upload: 'success', ocr: 'success', chunk: 'success', index: 'running' },
  },
  {
    id: 'task-2',
    fileName: '产品需求资料.zip',
    progress: 100,
    stage: '处理完成',
    status: 'success',
    steps: { upload: 'success', ocr: 'success', chunk: 'success', index: 'success' },
  },
  {
    id: 'task-3',
    fileName: '扫描合同附件.pdf',
    progress: 28,
    stage: 'OCR 识别失败',
    status: 'failed',
    steps: { upload: 'success', ocr: 'failed', chunk: 'waiting', index: 'waiting' },
  },
];
