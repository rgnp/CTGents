# stage=discover 模式补充

> 仅当 stage=discover 时加载，追加到 workflow 收尾之前

---

## discover 收尾

阶段 0 完成后立即停止。不进入阶段 1-4A。

输出候选清单后，对每篇候选论文问用户三个问题以推进：
1. domain 确认（当前标记为 TBD 的填真实 domain）
2. 优先级（哪些先下载）
3. 是否全量推进到 full pipeline

不替用户做优先级判断——候选论文可能 15 篇，用户可能只想下 5 篇。
