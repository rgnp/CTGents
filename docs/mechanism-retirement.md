# 机制退役台账

> 原则：基于生产者、产物、消费者和效果证据退役机制；低频或冷启动本身不是删除理由。

## 状态

- `protected`：新接通或承担硬闭环，样本少也不得删除；
- `observe`：有入口或消费者，但效果尚未形成决策证据；
- `retire`：入口断裂、无消费者、被完整替代，或输出已证明不能承担承诺职责；
- `retired`：入口、实现、测试和现役文档叙事已经一起移除。

## 2026-07-27 第一批

| 机制 | 生产者 | 消费者 | 证据 | 处置 |
|---|---|---|---|---|
| `check_project` 聚合总分/评级 | 六维启发式分数与额外加分 | CI `>=80` 门 | 同时报告 3 类红色问题却输出 `100/100 → 优秀`，红色问题可被加分掩盖 | retired |
| CI `spec-check` 分数门 | 解析 `总分` | PR/push | 唯一判据是上述失真总分；Ruff、pytest、docs-sync 已承担确定性门 | retired |
| `lint.execute()` 零工具调度壳 | `TOOLS_LINT=[]`，仍暴露 executor | 工具注册链 | 三个 schema 已于 2026-06-23 删除，executor 只能匹配模型不可见的工具名 | retired |
| `generate_agents_md()` 公共兼容壳 | lint 模块 | 只有自身单测 | 真实 `check_project(fix=True)` 使用内部 `_generate_agents_md_content` | retired |
| `docs/features.md` 历史功能快照 | 人工维护 | 无现役代码消费者 | 大量列出已移除的 `/mode`、`/trust`、自装插件和多模型路由，和当前代码冲突 | retired |
| Dashboard | `dashboard/` server + collectors | 人工启动的只读网页 | 用户明确不需要独立面板；它不参与 agent 闭环，底层统计已有命令与 Gap 消费 | retired |
| reflection JSON | `_finalize_session → reflect_on_session` | 仅 Dashboard | Dashboard 删除后无消费者，且内容可由 tracker 原始事件和异常检测派生 | retired |

保留的部分：

- `check_project()` 继续由 `make check` 输出具体问题，但不再生成质量总分或评级；
- `docs_sync_check()` 继续由 CI 直接调用；
- `_generate_agents_md_content()` 继续服务 `check_project(fix=True)`；
- tracker 原始工具事件、基线、异常检测和 Gap 消费链继续保留；
- Ruff、pytest、文档同步检查仍是确定性质量门。

## 保护项

| 机制 | 理由 |
|---|---|
| 统一工作回执 | 刚完成生产自举，已有 Task 回执和版本漂移验证 |
| 资产 helpful/misleading | 已接通完整证据链，正处真实反馈冷启动 |
| Heartbeat accept/revise/reject | 已有明确用户消费者，尚待真实无人期样本 |
| performance Gap 收益窗口 | 需要跨会话暴露机会，短期无事件不代表无效 |

## 观察项

| 机制 | 当前消费者 | 需要观察的问题 |
|---|---|---|
| `/organs` 旧体征 | 人工 `/organs` 查询 | 除共享工作回执外的体征是否触发过行动 |
| `check_project` 具体诊断 | `make check` | 目录排除与边界启发式是否持续误报 |

观察项没有效果证据前不扩建；没有无效证据前也不删除。
