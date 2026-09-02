# 检索评测语料

本目录中的 `retrieval_baseline.json` 与 `retrieval_realistic_v2.json` 都是合成工程回归数据，不代表真实企业文档效果，也不得在报告中称为“真实企业评测”。CI 继续使用这类可公开、可重复的数据检查检索算法是否退化。

`experience_regression_v4.json` 在 v3 的 22 条真实体验问题上增加了表达不变性分组和意义变化反例，只记录脱敏问题、期望关键词、来源文件名与禁止事实，不包含用户文档正文。`run_experience_eval.py` 会先使用 SQLite 在线备份读取本机知识库，再只对临时副本执行检索和回答验证，不会修改用户现有数据库。它不可替代经授权、脱敏和人工复核的企业评测语料，也不会加入不含真实文档的公共 CI。

v4 除逐题通过率外，还会计算同义问法的表达一致率、预期来源集合的一致率、意义变化反例准确率和查询漂移率。门禁失败时命令返回非零状态；这让后续查询改写必须同时证明“能召回同义表达”和“不会抹掉离职、奖金、加班费等关键限制”。

快速检查检索、来源和上下文分流：

```bash
npm run api:eval:experience:fast
```

完整检查本地模型回答、引用、关键词和跨题污染：

```bash
npm run api:eval:experience
```

Windows 默认读取 `%APPDATA%/com.zhiyu.knowledgehub`，macOS 默认读取 `~/Library/Application Support/com.zhiyu.knowledgehub`。也可以追加 `-- --data-dir /absolute/path` 指向另一份本地数据目录。生产索引若使用 `ollama:bge-m3`，运行评测时必须保持对应 Ollama Embedding 可用，以避免使用不同向量空间得出无效结论。

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
