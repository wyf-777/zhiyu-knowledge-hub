# 检索评测语料

本目录中的 `retrieval_baseline.json` 与 `retrieval_realistic_v2.json` 都是合成工程回归数据，不代表真实企业文档效果，也不得在报告中称为“真实企业评测”。CI 继续使用这类可公开、可重复的数据检查检索算法是否退化。

## 接入脱敏企业语料

1. 在组织内部完成授权、脱敏和人工复核；不要把原始企业文档、个人信息、密钥或商业机密提交到仓库。
2. 将脱敏后的文档正文和人工标注问题整理为与现有 JSON 相同的 `documents`、`cases` 结构。
3. 添加来源声明：

```json
{
  "version": "organization-private-v1",
  "provenance": {
    "type": "sanitized-enterprise",
    "organization": "anonymous",
    "reviewed_at": "2026-08-29"
  },
  "knowledge_base_id": "enterprise",
  "documents": [],
  "cases": []
}
```

4. 将私有评测文件放在仓库外或受访问控制且被 `.gitignore` 排除的位置，然后运行：

```bash
npm run api:eval -- --dataset /absolute/path/to/sanitized-enterprise.json --require-real-corpus
```

`--require-real-corpus` 会拒绝缺少来源声明、声明为合成数据或未填写复核日期的数据集。评测过程只在本机临时目录建立索引，不会调用公网服务；如选择 Ollama Embedding，地址仍只允许本机回环地址。
