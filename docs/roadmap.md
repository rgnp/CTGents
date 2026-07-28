# 路线图：自进化 = 越用越懂你

> 本项目对"自进化"的定义：**不是 agent 改自己的代码**——改代码是普通编码能力，按提示改就行。
> 自进化 = **越用越懂你**：跨会话积累对你、对这摊活的理解，下次更省心、更对味。

---

## 已落地的"懂你"器官

- **记忆**（`remember`/`recall`/`forget`）：显式写入，非自动收割。fingerprint 去重合并；
  同名更新记录 revision；矛盾替代项退出召回；forget 进入可恢复归档；旧记忆按时间衰减降权。
- **资产审计**：`memory_audit` 报告陈旧/冲突/重复候选，`knowledge_audit` 报告索引过期、
  空短文档、精确重复和 registry 断链。两者还汇总资产被检索、明确采用和参与任务结果的次数，
  但只报告、不自动删除用户资产。
- **使用证据链**：`recall`/`rag_search` 为真正返回的资产记 `retrieved`；agent 通过
  `adopt_asset` 声明具体用途后才记 `adopted`；任务验收、失败或放弃机械关联 `outcome`；
  `feedback_asset` 只对已有结果的采用显式记录 `helpful`/`misleading` 及理由。
- **失败教训**（`lesson`）：显式 remember 驱动，命中同类场景时尾部提醒。
- **recall**：跨记忆库关键词/bigram 检索——**非语义检索，无 embedding**（`memory/.embeddings/cache.json` 是废弃遗留文件，代码里无引用）。
- **`/organs`**：器官生命体征——派生自真实产物，一眼看哪个器官在跳、哪个衰竭。

## 已知最薄弱

**学习资产已有价值反馈，但样本仍稀疏**：现在可以区分检索、明确采用、任务结果和显式
`helpful`/`misleading`。审计会报告误导、反复检索未采用和待反馈候选，但不会自动降权或删除。
后续重点不是再加字段，而是积累真实反馈，观察候选规则是否稳定、有无误伤。

其次，跨会话记忆（`memory/`）检索还是词面匹配，没有语义层：换个说法可能搜不到同一个教训。
`memory/` 目前 20 余篇，量小不明显，量大了会成为瓶颈。触发点到了再上，值得提前记一笔。

（`knowledge/` 研究文档索引已在 2026-07-08 接了本地 embedding 语义层，见下方"已落地"——
这条只剩 `memory/` 侧的检索没跟上，不是全局问题了。）

## 已落地：研究知识库语义检索（2026-07-08）

`knowledge/`（400+ 文件、60+ 篇论文笔记）规模已经大到词面检索会漏召回——同一个概念换个
说法（比如中英文切换、"world model" vs "环境建模"）就搜不到。`index_research_content` /
`query_research`（`rag.py`）现在叠加一层本地 embedding（`embeddings.py`，默认
`paraphrase-multilingual-MiniLM-L12-v2`，离线、不要 API key），词面分数和向量余弦分数按
`RAG.lexical_weight` 加权合并。sentence-transformers 未安装/模型加载失败/`CTG_RAG_EMBED_ENABLED=0`
时自动退回纯 TF-IDF，行为和加这层之前完全一致——这层只加分，从不是单点故障。

## 已移除

- **agent 改自己代码的"进化 run 闭环"**（`evolve` / `evolution_runner` / `/evolve`）——
  这是"自进化 = 改代码"的旧定义。改代码本就是普通编码能力，不需要专门的进化机制，故整套移除。
- **会话结束自动收割**（`user_model` / `project_model`，2026-06-23）——LLM 每会话全量重写关于用户的
  断言，防编造只靠 prompt 软指令，无机械审计牙齿，属信任盲区；改为记忆完全由 agent 显式 `remember`
  驱动，可追溯到具体一次调用，不再有"整段被谁悄悄改写"的问题。`user_model`/`project_model` 这两个
  概念本身也已从代码中完全移除（非仅移除触发时机），当前无替代实现。
