# CTGents 监控面板（只读 MVP）

与 agent 进程**完全解耦**的可观测层。它只读磁盘上的 artifact，不注入 agent 上下文、不碰前缀缓存、不参与 LLM 循环。

## 运行

```bash
python -m dashboard.server          # 默认 127.0.0.1:8765
python -m dashboard.server --port 9000
```

然后浏览器打开 `http://127.0.0.1:8765`。页面顶部四个标签页，每 5 秒自动刷新当前页。

**agent 随便重启**——本进程读盘上的文件，互不牵连（无共享生命周期）。起一次挂那儿即可。

## 四视图 / 四接口

每个接口由 `collectors.py` 的一个 `build_*` 聚合（容错：单个采集器失败隔离为该块 `_error`，不拖垮整页）。

| 接口 | 视图 | 含面板 | 数据源 |
|---|---|---|---|
| `/api/overview` | 总览 | 健康判定 · 命中率/Token/请求/异常/门审计 · 正在做 · 各会话命中率 | `stats/{sid}.json`（命中率，直接读盘）+ `tasks/current.md` + `tasks/ambitions.md` + 门审计 + 自反思异常计数 |
| `/api/safety` | 安全门禁 | 门审计大状态 · 检查状态（pre-commit/测试/lint）· 风险提示 | `src.gate_audit.head_gate_notice`（门）；测试/lint 无落盘 artifact → `unknown`（不主动跑）；风险 = 门禁失败 / 受保护文件改动（`git status` ∩ `src.guard.PROTECTED_FILES`）/ 命中率下滑 / 高频失败 |
| `/api/memory` | 记忆教训 | lessons（指纹/频次）· 野心分区 · 自反思异常 | `memory/*.md`（复用 `_split_frontmatter`）+ `tasks/ambitions.md` + `stats/*_reflection.json` |
| `/api/evolution` | 进化日志 | 提交时间线 · 全部任务（当前+归档）· 最慢/最易失败工具 · 改进方向 | `git log` + `ARCHIVE_DIR` + `src.tracker` 基线 + `.gap_cache.json`（复用 `_load_gap_cache`，不触发 5s 冷算）|

session id = 最新合法 `stats/{sid}.json`（日期前缀命名；`test-verify.json` 等非会话统计已排除，不计入命中率）。

## 设计约束（别破坏）

1. **只读优先**：MVP 不放任何触发按钮（跑进化 / `/fix`）。先做"观测"，控制后置——加按钮 = 给 agent 加新的可被改写/出错的面，违背"开发期保持精简"。
2. **进程解耦**：`agent 不 import、不启动本 server`；本 server 单向读盘，不往 agent 推任何东西。维持这条 = 重启零负担、缓存零影响。
3. **不拉入 LLM 栈**：缓存命中率刻意直接读 `stats/{sid}.json`，不 import `src.llm`（那会把 API 客户端栈拉进监控进程）。
4. **不重写解析**：非平凡解析（frontmatter / gap 缓存 / 门审计）复用 `src` 的读函数，不另起一套 = 不制造漂移。
5. **排除出进化 scope**：`dashboard/` 不应进入 agent 的自进化改写范围——你正用它看 agent，别让 agent 重写自己的仪表盘。
