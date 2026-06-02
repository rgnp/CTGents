# 工具链进化日志

> 每次自检修复完成后写入一条。此后不在对话中复述修复细节，仅引用此日志。
> 记录格式：时间 → 工具 → 根因 → 修复 → 收益

---

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
