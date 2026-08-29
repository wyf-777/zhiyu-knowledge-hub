import { citations } from '../data/mockData';
import type { Citation } from '../types';

export interface MockStreamResult {
  citations: Citation[];
  suggestions: string[];
  noEvidence: boolean;
}

const defaultAnswer =
  '根据当前知识库中的相关制度，上海属于一类城市。普通员工住宿上限为 **500 元/晚**，部门负责人为 **650 元/晚**，超标部分原则上由个人承担。\n\n具体说明如下：\n\n- 普通员工：住宿费标准上限为 500 元/人/晚；\n- 部门负责人：住宿费标准上限为 650 元/人/晚；\n- 超出标准部分：原则上由个人承担，特殊情况需提前申请。\n\n如需报销，请在出差结束后 5 个工作日内提交报销申请，并附上住宿发票和行程单。';

const noEvidenceAnswer =
  '当前知识库中没有找到足够可靠的依据来回答这个问题。\n\n你可以尝试补充更具体的关键词、指定相关文档，或先上传包含该信息的资料。为了避免误导，我不会在缺少依据时推测答案。';

function delay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timer);
        reject(new DOMException('The operation was aborted.', 'AbortError'));
      },
      { once: true },
    );
  });
}

export class MockChatService {
  async streamAnswer(
    question: string,
    signal: AbortSignal,
    onChunk: (chunk: string) => void,
  ): Promise<MockStreamResult> {
    await delay(420, signal);
    if (/服务异常|网络异常/.test(question)) {
      throw new Error('Mock local service unavailable');
    }
    const noEvidence = /没有依据|不存在|查不到|无相关/.test(question);
    const answer = noEvidence ? noEvidenceAnswer : defaultAnswer;
    const chunks = answer.match(/.{1,7}/gs) ?? [answer];

    for (const chunk of chunks) {
      await delay(28 + Math.round(Math.random() * 18), signal);
      onChunk(chunk);
    }

    return {
      citations: noEvidence ? [] : citations,
      suggestions: noEvidence ? ['当前知识库中有哪些相关规定？', '请列出相关的原文依据。'] : ['查看完整报销流程', '不同职级标准对比', '超标住宿如何申请'],
      noEvidence,
    };
  }
}

export const mockChatService = new MockChatService();
