# 工具链进化日志

> 每次自检修复完成后写入一条。此后不在对话中复述修复细节，仅引用此日志。
> 记录格式：时间 → 工具 → 根因 → 修复 → 收益

---

### 记忆系统：全量注入 → 按需召回（~500 tokens 节省 / 每轮）

- **触发**：记忆越多，每次注入的索引越膨胀
- **根因**：`_make_memory_context()` 在启动和 `/clear` 时全量 dump 所有记忆到 context，线性增长
- **参考**：Claude "memory vs compaction 分离" — 记忆应单独管理，不应和对话历史混合
- **修复**：移除 startup 和 `/clear` 中的 `_make_memory_context()` 调用。env message 改为简短提示 "你拥有长期记忆，需要时用 recall 搜索"。`remember` 后仍按需注入更新后的记忆上下文。
- **收益**：每轮对话节省 ~500+ tokens（记忆索引全量 dump 的开销）；缓存命中不受影响

### 工具结果：添加可压缩标记 → 压缩时可针对性截断

- **触发**：工具结果（read_file 等）大量占据保留窗口
- **根因**：工具结果在压缩后仍以完整形式保留，已消费的内容不必要
- **参考**：Claude "tool-result clearing" — 工具结果被消费后应清理/压缩
- **修复**：(1) 工具结果存储时加 `_tool_name` + `_tool_result_compressed` 内部标记 (2) 压缩后处理阶段对仍保留的过长工具结果做二次截断
- **收益**：长工具结果在压缩后进一步缩减，保留窗口可容纳更有价值的对话内容
## 2026-06-02

### read_file_lines / read_file 高频缓存

- **触发**：5 次连续 read_file_lines 同一文件
- **根因**：每次调用 `path.read_text().split("\n")` 做完整磁盘 I/O
- **修复**：新增 `_read_cached(path)` 通用函数，基于 `(path, mtime)` 验证，TTL=60s，最大 50 条目。`read_file()` 和 `read_file_lines()` 共用同一缓存。
- **收益**：同文件 60s 内多次读取只做 1 次磁盘 I/O
- **文件**：`src/tools/file.py`

### git_review 慢工具（~20s）

- **触发**：2 次调用均耗时 ~20s
- **根因**：纯文档（.md）变更也走完整 LLM 审查管线
- **修复**：新增 `CODE_EXTENSIONS` 白名单，`.py/.rs/.go/...` 才触发 LLM，纯文档直接跳过
- **收益**：纯文档提交 ~20s → ~0.5s；代码提交不受影响
- **文件**：`src/tools/git.py`

### get_project_context 无缓存 → 前缀重建重复扫描项目

- **触发**：前缀每次重建（/new / /clear）都扫描项目文件系统
- **根因**：`get_project_context()` 无缓存，`_detect_language_and_framework()` 每次做文件 I/O
- **修复**：加 300s TTL 缓存（`_project_context_cache`），基于 path 匹配。同时保证 env + project 字节稳定 → 不破坏 DeepSeek 前缀缓存命中
- **收益**：同一会话内多次前缀重建不做重复扫描
- **文件**：`src/tools/project.py`

---

## 2026-06-01

### read_page 慢工具（~10.6s）

- **触发**：4 次调用均耗时 ~10658ms
- **根因**：(1) `resp.read()` 无限制下载完整 HTML (2) trafilatura 含表格提取的完整管线 (3) HTTP 超时 15s 过宽松
- **修复**：(1) `resp.read(300KB)` 硬截断 (2) trafilatura 加 `include_tables=False` (3) `_FETCH_TIMEOUT` 15s→8s
- **收益**：预期 10.6s → 3-6s
- **文件**：`src/tools/web.py`
