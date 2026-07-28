# stage=resume 模式补充

> 仅当 stage=resume 时加载，注入到 workflow 执行前

---

## resume 前置

读 `_pipeline_state.md` 后，汇报断点状态再执行：

```
从断点恢复 — YYYY-MM-DD

上次遗留:
- 阶段 0 pending（pdf_pending + download_error）: N 篇 → 先测网络，可达则从阶段 1 重试
- 阶段 1（有 PDF 无 paper.md）: M 篇 → 从阶段 2 开始
- 阶段 2（有 paper.md 无 _meta 或 ingest=pending）: K 篇 → 从阶段 3 开始

本次将处理: N+M+K 篇
```

### 网络可达时

阶段 0 pdf_pending 论文按阶段 1（下载）重试——不需要重新搜索。

### 网络不可达时

跳过阶段 0 pending 论文（保持 pdf_pending），只推进阶段 1 和阶段 2 的论文。
在收尾报告中标注"K 篇下载失败的论文等待网络恢复"。

---

## resume 收尾

额外汇报：
- 上次卡住的论文中，本次成功的篇数
- 仍然卡住的论文和原因
